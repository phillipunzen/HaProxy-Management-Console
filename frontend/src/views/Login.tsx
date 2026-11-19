import {
  ArrowRight,
  Check,
  KeyRound,
  Loader2,
  LockKeyhole,
  Network,
  Server,
  ShieldCheck,
} from 'lucide-react';
import { useState } from 'react';
import { api } from '../api';
import { Button, Field } from '../components/ui';

export function Login({ onLogin }: { onLogin: (r: any) => void }) {
  const [username, setUsername] = useState(''),
    [password, setPassword] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  return (
    <div className="login-layout">
      <div className="login-aside">
        <a className="brand" href="#">
          <span className="brand-mark">
            <Network size={23} />
          </span>
          <span>
            HAProxy<span className="brand-sub">CONTROL</span>
          </span>
        </a>
        <div className="login-pitch">
          <span className="eyebrow">DEINE INFRASTRUKTUR. EIN WORKSPACE.</span>
          <h1>
            Alles verbunden.
            <br />
            Alles im Blick.
          </h1>
          <p>
            Hosts, Zertifikate und Traffic.
            <br />
            Zentrale Kontrolle über deine HAProxy-Server.
          </p>
          <div className="network-art">
            <div className="network-node center">
              <Network size={35} />
              <span>CONTROL</span>
            </div>
            <div className="network-line first" />
            <div className="network-line second" />
            <div className="network-line third" />
            {['Docker', 'Nativ', 'Docker'].map((n, i) => (
              <div key={i} className={'network-node leaf leaf-' + i}>
                <Server size={25} />
                <span>{n}</span>
                <i />
              </div>
            ))}
          </div>
        </div>
        <span className="login-aside-foot">
          <ShieldCheck size={15} /> Konfiguration prüfen. Sicher anwenden.
        </span>
      </div>
      <div className="login-main">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            try {
              onLogin(await api('/auth/login', 'POST', { username, password }));
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <span className="login-lock">
            <LockKeyhole size={25} />
          </span>
          <h2>Willkommen zurück</h2>
          <p>Melde dich in deinem Management-Workspace an.</p>
          {error && (
            <div className="notice error" role="alert">
              {error}
            </div>
          )}
          <Field label="Benutzername">
            <input
              required
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Dein Benutzername"
              maxLength={80}
            />
          </Field>
          <Field label="Passwort">
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Dein Passwort"
              maxLength={200}
            />
          </Field>
          <Button disabled={busy} type="submit">
            {busy ? (
              <Loader2 size={17} className="spin" />
            ) : (
              <>
                Anmelden <ArrowRight size={17} />
              </>
            )}
          </Button>
          <div className="login-help">
            <ShieldCheck size={15} />
            <span>Zugriff nur für autorisierte Benutzer</span>
          </div>
        </form>
        <span className="login-footer">HAProxy Control · Infrastrukturverwaltung</span>
      </div>
    </div>
  );
}
export function PasswordScreen({ onDone }: { onDone: () => void }) {
  return (
    <div className="password-screen">
      <section className="panel">
        <span className="login-lock">
          <KeyRound size={25} />
        </span>
        <h2>Dein persönliches Passwort</h2>
        <p>Ersetze das Startpasswort, bevor du deine Server verwaltest.</p>
        <PasswordForm onDone={onDone} />
      </section>
    </div>
  );
}
export function PasswordForm({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState(''),
    [password, setPassword] = useState(''),
    [repeat, setRepeat] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  return (
    <form
      className="form-body"
      onSubmit={async (e) => {
        e.preventDefault();
        if (password !== repeat) {
          setError('Die neuen Passwörter stimmen nicht überein.');
          return;
        }
        setBusy(true);
        try {
          await api('/auth/password', 'POST', { current_password: current, password });
          onDone();
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <Field label="Aktuelles / Startpasswort">
        <input
          type="password"
          required
          autoComplete="current-password"
          maxLength={200}
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </Field>
      <Field label="Neues Passwort" hint="Mindestens 10 Zeichen. Danach erneut anmelden.">
        <input
          type="password"
          required
          minLength={10}
          maxLength={200}
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </Field>
      <Field label="Neues Passwort wiederholen">
        <input
          type="password"
          required
          minLength={10}
          maxLength={200}
          autoComplete="new-password"
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
        />
      </Field>
      {error && <div className="notice error">{error}</div>}
      <Button disabled={busy} type="submit">
        {busy ? <Loader2 size={16} className="spin" /> : <Check size={16} />}Passwort speichern
      </Button>
    </form>
  );
}
