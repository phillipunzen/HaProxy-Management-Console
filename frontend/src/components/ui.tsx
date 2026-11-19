import { Circle, Network, X } from 'lucide-react';
import React, { createContext, useContext, useEffect, useId, useRef } from 'react';
import { TargetNotice } from '../Infrastructures';
import { type Instance } from '../types';

export function Button({
  children,
  secondary = false,
  danger = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { secondary?: boolean; danger?: boolean }) {
  return (
    <button className={`button ${secondary ? 'secondary' : ''} ${danger ? 'danger' : ''}`} {...props}>
      {children}
    </button>
  );
}
export function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: string }) {
  return <span className={'badge ' + tone}>{children}</span>;
}
export function Status({ instance }: { instance: Instance }) {
  const fresh =
    instance.collected_at &&
    Date.now() - Date.parse(instance.collected_at) < (instance.fresh_for_seconds || 90) * 1000;
  return (
    <Badge tone={fresh && instance.stats?.online ? 'green' : !fresh ? 'neutral' : 'red'}>
      <Circle size={7} fill="currentColor" />
      {!fresh ? 'Unbekannt' : instance.stats?.online ? 'Online' : 'Nicht erreichbar'}
    </Badge>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {React.isValidElement(children)
        ? React.cloneElement(children as React.ReactElement<{ id?: string; 'aria-describedby'?: string }>, {
            id,
            'aria-describedby': hint ? id + '-hint' : undefined,
          })
        : children}
      {hint && <small id={id + '-hint'}>{hint}</small>}
    </div>
  );
}
export const ModalContext = createContext<{ target: Instance | null; busy: boolean }>({
  target: null,
  busy: false,
});
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const context = useContext(ModalContext),
    dialogRef = useRef<HTMLElement>(null),
    close = () => {
      if (!context.busy) onClose();
    },
    closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const controls = () =>
      Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>('button,input,select,textarea') || [],
      ).filter((e) => !e.matches(':disabled'));
    controls()[0]?.focus();
    const fn = (e: KeyboardEvent) => {
      if (Array.from(document.querySelectorAll('.overlay .modal')).at(-1) !== dialogRef.current) return;
      if (e.key === 'Escape') closeRef.current();
      const focus = controls();
      if (e.key === 'Tab' && focus.length) {
        if (e.shiftKey && document.activeElement === focus[0]) {
          e.preventDefault();
          focus.at(-1)?.focus();
        } else if (!e.shiftKey && document.activeElement === focus.at(-1)) {
          e.preventDefault();
          focus[0].focus();
        }
      }
    };
    window.addEventListener('keydown', fn);
    return () => {
      window.removeEventListener('keydown', fn);
      previous?.focus();
    };
  }, []);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={'modal ' + (wide ? 'wide' : '')}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon-button" disabled={context.busy} aria-label="Schließen" onClick={close}>
            <X size={20} />
          </button>
        </header>
        {context.target && <TargetNotice target={context.target} />} {children}
      </section>
    </div>
  );
}
export function Empty({
  icon: Icon = Network,
  title,
  text,
  action,
}: {
  icon?: typeof Network;
  title: string;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon size={30} />
      </span>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
