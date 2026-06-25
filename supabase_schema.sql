-- À exécuter dans Supabase SQL Editor avant déploiement.
create extension if not exists "pgcrypto";

create table if not exists public.sterilization_cycles (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  lot_number text not null unique,
  operator_name text not null,
  operator_role text,
  autoclave_name text not null,
  autoclave_serial text,
  cycle_number text not null,
  cycle_type text not null,
  process_date date,
  packaging_mode text,
  dlu_date date,
  devices jsonb not null default '[]'::jsonb,
  quantity integer not null default 0,
  indicators jsonb not null default '{}'::jsonb,
  status text not null default 'À surveiller',
  notes text,
  qr_payload text
);

create table if not exists public.patient_traceability_records (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  patient_name text not null,
  patient_external_id text,
  care_date date,
  practitioner text,
  act text,
  room text,
  lot_numbers jsonb not null default '[]'::jsonb,
  cycles_snapshot jsonb not null default '[]'::jsonb,
  status text not null default 'À surveiller',
  notes text
);

create table if not exists public.audit_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  event_type text not null,
  actor text,
  target text,
  payload jsonb not null default '{}'::jsonb
);

create index if not exists idx_sterilization_cycles_lot on public.sterilization_cycles(lot_number);
create index if not exists idx_sterilization_cycles_created_at on public.sterilization_cycles(created_at desc);
create index if not exists idx_patient_records_created_at on public.patient_traceability_records(created_at desc);

alter table public.sterilization_cycles enable row level security;
alter table public.patient_traceability_records enable row level security;
alter table public.audit_events enable row level security;

-- Prototype : ouvert pour faciliter le test avec une clé anon.
-- Usage réel : remplacez par des politiques authentifiées strictes.
drop policy if exists "prototype_read_cycles" on public.sterilization_cycles;
create policy "prototype_read_cycles" on public.sterilization_cycles for select using (true);
drop policy if exists "prototype_insert_cycles" on public.sterilization_cycles;
create policy "prototype_insert_cycles" on public.sterilization_cycles for insert with check (true);

drop policy if exists "prototype_read_patient_records" on public.patient_traceability_records;
create policy "prototype_read_patient_records" on public.patient_traceability_records for select using (true);
drop policy if exists "prototype_insert_patient_records" on public.patient_traceability_records;
create policy "prototype_insert_patient_records" on public.patient_traceability_records for insert with check (true);

drop policy if exists "prototype_read_audit" on public.audit_events;
create policy "prototype_read_audit" on public.audit_events for select using (true);
drop policy if exists "prototype_insert_audit" on public.audit_events;
create policy "prototype_insert_audit" on public.audit_events for insert with check (true);
