-- Migración aplicada en Supabase: métricas de uso y aceptación de la aplicación automática de la memoria.
-- (La tabla de administradoras se completa a mano: insert into privado.administradora select id from auth.users where email = '<mail>';)
create table if not exists privado.administradora (user_id uuid primary key references auth.users(id) on delete cascade);

create or replace function privado.es_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from privado.administradora a where a.user_id = (select auth.uid()));
$$;
grant execute on function privado.es_admin() to authenticated;
create or replace function public.soy_admin() returns boolean
language sql stable security invoker set search_path = '' as $$ select privado.es_admin(); $$;
grant execute on function public.soy_admin() to authenticated;

-- un evento por cada «Cruzar» (se guarde o no el cruce)
create table if not exists public.evento_cruce (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace(id) on delete cascade,
  usuario_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  firma_archivos text, origen text not null default 'heuristica', memoria_confianza integer,
  aceptada boolean, corregida boolean, segundos_preparacion numeric, segundos_cruce numeric,
  n_grupos integer, n_archivos integer, filas integer, llaves integer, con_diferencias integer,
  llamadas_ia integer not null default 0, tokens_ia integer not null default 0, ia_llave_validada boolean,
  idioma text, created_at timestamptz not null default now()
);
alter table public.evento_cruce enable row level security;
create policy "registrar mis eventos" on public.evento_cruce for insert to authenticated
  with check (privado.es_miembro(workspace_id) and usuario_id = (select auth.uid()));
create policy "ver eventos de mis workspaces o todos si soy admin" on public.evento_cruce for select to authenticated
  using (privado.es_miembro(workspace_id) or privado.es_admin());

-- aceptación explícita, por usuario, de aplicar sola una configuración aprendida (confianza > 95 %)
create table if not exists public.memoria_autoaplicar (
  memoria_id uuid not null references public.memoria_conciliacion(id) on delete cascade,
  usuario_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (memoria_id, usuario_id)
);
alter table public.memoria_autoaplicar enable row level security;
create policy "mis aceptaciones" on public.memoria_autoaplicar for all to authenticated
  using (usuario_id = (select auth.uid())) with check (usuario_id = (select auth.uid()));

create policy "admin ve la memoria de todos" on public.memoria_conciliacion for select to authenticated using (privado.es_admin());
create policy "admin ve los cruces de todos" on public.conciliacion for select to authenticated using (privado.es_admin());
