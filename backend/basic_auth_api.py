from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select,delete,func
from sqlalchemy.exc import IntegrityError

from backend import basic_auth as auth
from backend.db import get_db,BasicAuthUser,BasicAuthGroup,BasicAuthMembership,BasicAuthDeployment,Instance
from backend.schemas import BasicAuthUserIn,BasicAuthGroupIn,Document


def public_user(db,value):
    return {'id':value.id,'username':value.username,'enabled':value.enabled,'version':value.version,
            'group_ids':list(db.scalars(select(BasicAuthMembership.group_id).where(BasicAuthMembership.user_id==value.id).order_by(BasicAuthMembership.group_id)))}


def members(db,user_id,group_ids):
    if set(db.scalars(select(BasicAuthGroup.id).where(BasicAuthGroup.id.in_(group_ids)).with_for_update()))!=set(group_ids):raise HTTPException(422,'Eine ausgewählte Basic-Auth-Gruppe existiert nicht.')
    db.execute(delete(BasicAuthMembership).where(BasicAuthMembership.user_id==user_id))
    db.add_all([BasicAuthMembership(user_id=user_id,group_id=id) for id in group_ids])


def finish(db):
    try:db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Dieser Benutzer- oder Gruppenname ist bereits vergeben.')


def router(admin,operator,audit):
    api=APIRouter(prefix='/api/basic-auth')

    @api.get('')
    def directory(user=Depends(operator),db=Depends(get_db)):
        usage=auth.assignments(db);group_values=list(db.scalars(select(BasicAuthGroup).order_by(BasicAuthGroup.name)))
        membership_counts={id:count for id,count in db.execute(select(BasicAuthMembership.group_id,func.count()).group_by(BasicAuthMembership.group_id))}
        active_counts={id:count for id,count in db.execute(select(BasicAuthMembership.group_id,func.count()).join(BasicAuthUser).where(BasicAuthUser.enabled.is_(True)).group_by(BasicAuthMembership.group_id))}
        groups=[{'id':g.id,'name':g.name,'realm':g.realm,'description':g.description,'version':g.version,'members':membership_counts.get(g.id,0),'active_members':active_counts.get(g.id,0),'sites':[u for u in usage if u['group_id']==g.id]} for g in group_values]
        states=[]
        for i in db.scalars(select(Instance)):
            doc=Document.model_validate(i.document);deployment=db.get(BasicAuthDeployment,i.id)
            if not auth.ids(doc) and not deployment:continue
            try:
                expected=auth.metadata(doc,auth.snapshots(db,auth.ids(doc))) if auth.ids(doc) else {}
                applied=deployment.metadata_json if deployment else {}
                pending=expected!=applied
                reason=None
            except ValueError:pending=True;reason='Zugewiesene Gruppe fehlt.'
            states.append({'instance_id':i.id,'instance_name':i.name,'pending':pending,'reason':reason,'applied_at':deployment.applied_at.isoformat()+'Z' if deployment else None})
        return {'users':[public_user(db,u) for u in db.scalars(select(BasicAuthUser).order_by(BasicAuthUser.username))],'groups':groups,'instances':states}

    @api.post('/users',status_code=201)
    def create_user(body:BasicAuthUserIn,user=Depends(admin),db=Depends(get_db)):
        if not body.password:raise HTTPException(422,'Für einen neuen Basic-Auth-Benutzer ein Passwort angeben.')
        try:hashed=auth.hash_password(body.password)
        except ValueError as e:raise HTTPException(422,str(e))
        auth.lock_directory(db)
        if db.scalar(select(BasicAuthUser.id).where(BasicAuthUser.username==body.username).with_for_update()):raise HTTPException(409,'Basic-Auth-Benutzername bereits vergeben.')
        value=BasicAuthUser(username=body.username,password_hash=hashed,enabled=body.enabled);db.add(value);db.flush();members(db,value.id,body.group_ids)
        audit(db,user.username,'basic_auth.user.created',value.username);finish(db);return public_user(db,value)

    @api.put('/users/{id}')
    def update_user(id:int,body:BasicAuthUserIn,user=Depends(admin),db=Depends(get_db)):
        try:hashed=auth.hash_password(body.password) if body.password else None
        except ValueError as e:raise HTTPException(422,str(e))
        auth.lock_directory(db);value=db.scalar(select(BasicAuthUser).where(BasicAuthUser.id==id).with_for_update().execution_options(populate_existing=True))
        if not value:raise HTTPException(404,'Basic-Auth-Benutzer nicht gefunden.')
        if value.version!=body.version:raise HTTPException(409,'Benutzer wurde parallel geändert. Bitte neu laden.')
        if db.scalar(select(BasicAuthUser.id).where(BasicAuthUser.username==body.username,BasicAuthUser.id!=id).with_for_update()):raise HTTPException(409,'Basic-Auth-Benutzername bereits vergeben.')
        value.username=body.username;value.enabled=body.enabled;value.version+=1
        if hashed:value.password_hash=hashed
        members(db,id,body.group_ids);audit(db,user.username,'basic_auth.user.updated',value.username);finish(db);return public_user(db,value)

    @api.delete('/users/{id}')
    def remove_user(id:int,user=Depends(admin),db=Depends(get_db)):
        auth.lock_directory(db);value=db.scalar(select(BasicAuthUser).where(BasicAuthUser.id==id).with_for_update().execution_options(populate_existing=True))
        if not value:raise HTTPException(404,'Basic-Auth-Benutzer nicht gefunden.')
        # Explicit membership removal also works with SQLite test databases.
        db.execute(delete(BasicAuthMembership).where(BasicAuthMembership.user_id==id))
        audit(db,user.username,'basic_auth.user.removed',value.username);db.delete(value);finish(db);return {'ok':True}

    @api.post('/groups',status_code=201)
    def create_group(body:BasicAuthGroupIn,user=Depends(admin),db=Depends(get_db)):
        auth.lock_directory(db)
        if db.scalar(select(BasicAuthGroup.id).where(BasicAuthGroup.name==body.name).with_for_update()):raise HTTPException(409,'Basic-Auth-Gruppenname bereits vergeben.')
        value=BasicAuthGroup(**body.model_dump());db.add(value);db.flush();audit(db,user.username,'basic_auth.group.created',value.name);finish(db)
        return {'id':value.id,**body.model_dump()}

    @api.put('/groups/{id}')
    def update_group(id:int,body:BasicAuthGroupIn,user=Depends(admin),db=Depends(get_db)):
        auth.lock_directory(db);value=db.scalar(select(BasicAuthGroup).where(BasicAuthGroup.id==id).with_for_update().execution_options(populate_existing=True))
        if not value:raise HTTPException(404,'Basic-Auth-Gruppe nicht gefunden.')
        if value.version!=body.version:raise HTTPException(409,'Gruppe wurde parallel geändert. Bitte neu laden.')
        if db.scalar(select(BasicAuthGroup.id).where(BasicAuthGroup.name==body.name,BasicAuthGroup.id!=id).with_for_update()):raise HTTPException(409,'Basic-Auth-Gruppenname bereits vergeben.')
        for key in ('name','realm','description'):setattr(value,key,getattr(body,key))
        value.version+=1;audit(db,user.username,'basic_auth.group.updated',value.name);finish(db)
        return {'id':id,'name':value.name,'realm':value.realm,'description':value.description,'version':value.version}

    @api.delete('/groups/{id}')
    def remove_group(id:int,user=Depends(admin),db=Depends(get_db)):
        auth.lock_directory(db);value=db.scalar(select(BasicAuthGroup).where(BasicAuthGroup.id==id).with_for_update().execution_options(populate_existing=True))
        if not value:raise HTTPException(404,'Basic-Auth-Gruppe nicht gefunden.')
        if any(u['group_id']==id for u in auth.assignments(db,lock=True)):raise HTTPException(409,'Gruppe wird noch von Proxy-Sites verwendet. Zuordnungen zuerst ändern oder entfernen.')
        db.execute(delete(BasicAuthMembership).where(BasicAuthMembership.group_id==id))
        audit(db,user.username,'basic_auth.group.removed',value.name);db.delete(value);finish(db);return {'ok':True}

    return api
