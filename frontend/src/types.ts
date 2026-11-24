import { type AccessPolicy } from './AccessPolicy';
import { type AccessProfile } from './AccessProfiles';
import { type Certificate } from './Certificates';
import { type ImportedFields } from './ConfigImport';
import { type Frontend, type Pool } from './ProxyLayout';
import { type ProxyRow } from './ProxyStatistics';

export type User = { id: number; username: string; role: string; must_change_password: boolean };
export type Stats = {
  online: boolean;
  sessions?: number;
  request_rate?: number;
  requests?: number;
  bytes_in?: number;
  bytes_out?: number;
  errors_5xx?: number;
  version?: string;
  uptime?: string;
  error?: string;
  rows?: ProxyRow[];
  history_policy?: { raw_hours: number; fine_days: number; total_days: number };
};
export type Instance = {
  infrastructure_id: number | null;
  infrastructure_name: string | null;
  tags: string[];
  location: string;
  metadata_version: number;
  id: number;
  name: string;
  agent_url: string;
  profile: string;
  kind: string;
  notes: string;
  allow_http: boolean;
  stats: Stats | null;
  collected_at: string | null;
  fresh_for_seconds?: number;
};
export type Host = {
  access_profile?: string | null;
  access_paths?: string[];
  access_policy?: AccessPolicy | null;
  proxy_options?: string[];
  id: string;
  /** Readable HAProxy backend name, assigned by the server from domain and path. */
  name?: string | null;
  frontend?: string;
  certificate?: string | null;
  basic_auth_group?: number | null;
  basic_auth_forward?: boolean;
  domain: string;
  aliases?: string[];
  path: string;
  enabled: boolean;
  force_https: boolean;
  balance: string;
  servers: { address: string; port: number; weight: number; tls: boolean; tls_verify?: boolean }[];
};
export type Rule = {
  frontend?: string | null;
  preserve_path?: boolean;
  id: string;
  name: string;
  enabled: boolean;
  match: string;
  value: string;
  action: string;
  target: string;
  code: number;
};
export type Doc = ImportedFields & {
  access_profiles?: AccessProfile[];
  frontends?: Frontend[];
  backends?: Pool[];
  frontend_certificates?: Record<string, string[]>;
  http_port: number;
  https_port: number;
  tls_enabled: boolean;
  acme_enabled: boolean;
  acme_address: string;
  acme_port: number;
  maxconn: number;
  hosts: Host[];
  rules: Rule[];
  version: number;
};
export type Cert = Certificate;
export type Revision = { id: number; status: string; message: string; author: string; created_at: string };
export type Cap = {
  /** Agent can test backend targets and domains (check-targets / check-sites). */
  probes?: boolean;
  certificate_delete?: boolean;
  tls_site_bindings?: boolean;
  certificate_management?: boolean;
  acme_engines?: string[];
  certificate_scope_error?: string | null;
  kind: string;
  dns_providers: string[];
  http_challenge: boolean;
  runtime_socket_config: string;
  cert_dir_config: string;
};
export type Metric = {
  time: string;
  online: boolean;
  request_rate: number | null;
  sessions: number | null;
  request_rate_peak?: number | null;
  sessions_peak?: number | null;
  availability?: number | null;
  samples?: number;
  resolution_seconds?: number;
};
export type MetricStorage = {
  interval_seconds: number;
  retention: { raw_hours: number; fine_days: number; total_days: number };
  buckets: Record<string, number>;
  latest_rows: number;
  legacy_rows: number;
  max_buckets_per_instance: number;
  allocated_bytes?: number | null;
  legacy_table_bytes?: number | null;
};

export type PendingChange = {
  kind: string;
  key: string;
  action: 'added' | 'changed' | 'removed';
  type: string;
  label: string;
};
export type PendingChanges = {
  baseline: boolean;
  count: number | null;
  changes: PendingChange[];
  version: number;
};
/** What the apply dialog applies: the saved draft (default) or a text config from the editor. */
export type ApplyRequest = {
  id: number;
  config?: string;
  baseHash?: string;
  revision?: number | null;
  documentVersion?: number | null;
  message?: string;
};
export type DiffLine = {
  type: 'context' | 'added' | 'removed';
  text: string;
  old: number | null;
  new: number | null;
};
export type SiteCheck = {
  kind: string;
  key: string;
  label: string;
  state: 'ok' | 'warning' | 'error' | 'skipped';
  message: string;
  status?: number | null;
  ms?: number | null;
  url?: string;
};
export type ApplyPreview = {
  source: 'draft' | 'text';
  config: string;
  base_hash: string;
  document_version: number;
  diff: { hunks: { header: string; lines: DiffLine[] }[]; added: number; removed: number };
  valid: boolean;
  output: string;
  changes: PendingChange[] | null;
};
/** GET /instances/{id}/https/{kind}/{key}: what the HTTPS assistant can do for one entry. */
export type HttpsPlan = {
  kind: 'hosts' | 'imported_routes';
  key: string;
  label: string;
  domains: string[];
  wildcard: boolean;
  certificate: string | null;
  certificate_ok: boolean;
  redirect: boolean;
  force_https: boolean;
  existing: { name: string; domains: string[]; days_remaining: number | null }[];
  challenges: {
    http: { available: boolean; reason: string | null };
    dns: {
      available: boolean;
      reason: string | null;
      credentials: { id: string; provider: string; label: string }[];
    };
  };
  enable_challenge: boolean;
  challenge_live: boolean;
  challenge_target: string | null;
  enable_tls: boolean;
  suggested_name: string;
  suggested_email: string;
  pending: { kind: string; key: string; action: string; label: string; type: string }[];
  applied_known: boolean;
  blockers: string[];
  can_check_sites: boolean;
  version: number;
};
