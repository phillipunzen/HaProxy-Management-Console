"""Infrastructure organization; assignments share the server metadata version."""
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from backend.db import get_db,Infrastructure,InstanceInfrastructure
from backend.schemas import InfrastructureIn

def public(value,count=0):
    return {'id':value.id,'name':value.name,'description':value.description,'version':value.version,'servers':count}

def assignment(db,id):
    return db.scalar(select(Infrastructure).join(InstanceInfrastructure,InstanceInfrastructure.infrastructure_id==Infrastructure.id).where(InstanceInfrastructure.instance_id==id))

def assign(db,id,target):
    if target is not None and not db.scalar(select(Infrastructure.id).where(Infrastructure.id==target).with_for_update()):
        raise HTTPException(422,'Die ausgewählte Infrastruktur existiert nicht mehr.')
    link=db.scalar(select(InstanceInfrastructure).where(InstanceInfrastructure.instance_id==id).with_for_update().execution_options(populate_existing=True))
    if target is None:
        if link:db.delete(link)
    elif link:link.infrastructure_id=target
    else:db.add(InstanceInfrastructure(instance_id=id,infrastructure_id=target))

def finish(db):
    try:db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Infrastruktur ist bereits vorhanden oder ihre Server-Zuordnung wurde parallel geändert.')

def router(current_user,admin,audit):
    api=APIRouter(prefix='/api/infrastructures')

    @api.get('')
    def directory(user=Depends(current_user),db=Depends(get_db)):
        return [public(i,count) for i,count in db.execute(select(Infrastructure,func.count(InstanceInfrastructure.instance_id)).outerjoin(InstanceInfrastructure).group_by(*Infrastructure.__table__.columns).order_by(Infrastructure.name))]

    @api.post('',status_code=201)
    def create(body:InfrastructureIn,user=Depends(admin),db=Depends(get_db)):
        value=Infrastructure(name=body.name,name_key=body.name.casefold(),description=body.description,version=0);db.add(value)
        audit(db,user.username,'infrastructure.created',body.name);finish(db);return public(value)

    @api.put('/{id}')
    def update(id:int,body:InfrastructureIn,user=Depends(admin),db=Depends(get_db)):
        db.rollback()
        value=db.scalar(select(Infrastructure).where(Infrastructure.id==id).with_for_update().execution_options(populate_existing=True))
        if not value:raise HTTPException(404,'Infrastruktur nicht gefunden.')
        if value.version!=body.version:raise HTTPException(409,'Infrastruktur wurde parallel geändert. Bitte neu laden.')
        value.name=body.name;value.name_key=body.name.casefold();value.description=body.description;value.version+=1
        audit(db,user.username,'infrastructure.updated',value.name);finish(db)
        return public(value,db.scalar(select(func.count()).select_from(InstanceInfrastructure).where(InstanceInfrastructure.infrastructure_id==id)))

    @api.delete('/{id}')
    def remove(id:int,user=Depends(admin),db=Depends(get_db)):
        db.rollback()
        value=db.scalar(select(Infrastructure).where(Infrastructure.id==id).with_for_update().execution_options(populate_existing=True))
        if not value:raise HTTPException(404,'Infrastruktur nicht gefunden.')
        if db.scalar(select(InstanceInfrastructure.instance_id).where(InstanceInfrastructure.infrastructure_id==id).limit(1).with_for_update()):
            raise HTTPException(409,'Zuerst die zugewiesenen Server einer anderen Infrastruktur zuordnen oder die Zuordnung entfernen.')
        audit(db,user.username,'infrastructure.removed',value.name);db.delete(value);finish(db);return {'ok':True}

    return api
