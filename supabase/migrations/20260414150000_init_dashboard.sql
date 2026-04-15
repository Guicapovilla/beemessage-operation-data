create table if not exists public.operation_weeks (
  week_key text primary key,
  payload jsonb not null,
  updated_at timestamptz not null default timezone('utc', now()),
  constraint operation_weeks_week_key_format check (week_key ~ '^[0-9]{4}-W[0-9]{2}$')
);

create table if not exists public.dashboard_improvements (
  id smallint primary key,
  payload jsonb not null,
  updated_at timestamptz not null default timezone('utc', now()),
  constraint dashboard_improvements_single_row check (id = 1)
);

insert into public.dashboard_improvements (id, payload)
values (
  1,
  $json$
{
  "improvements": [
    {
      "id": "imp-001",
      "title": "Padronizar follow-up de leads",
      "area": "Comercial",
      "status": "em_producao",
      "priority": "alta",
      "created_at": "2026-03-10",
      "updated_at": "2026-04-01",
      "description": "O follow-up de leads não tem um padrão definido — cada colaborador executa de forma diferente, causando inconsistência no atendimento e perda de oportunidades por falta de cadência.",
      "action_plan": "1. Mapear o fluxo atual de follow-up por colaborador\n2. Identificar os pontos de abandono mais frequentes\n3. Criar template de sequência de follow-up (e-mail + WhatsApp)\n4. Documentar no Notion e treinar o time\n5. Monitorar taxa de conversão nas 4 semanas seguintes",
      "expected_result": "Redução de 30% no ciclo médio de fechamento e aumento de 20% na taxa de conversão de leads qualificados.",
      "metric_label": "Taxa de conversão de leads",
      "metric_before": 12.5,
      "metric_after": null,
      "metric_unit": "%"
    },
    {
      "id": "imp-002",
      "title": "Automatizar relatório semanal de operações",
      "area": "Gestão",
      "status": "em_teste",
      "priority": "media",
      "created_at": "2026-03-01",
      "updated_at": "2026-04-05",
      "description": "O relatório de operações era gerado manualmente toda semana, consumindo 2-3 horas de trabalho e com risco de inconsistências entre versões.",
      "action_plan": "1. Mapear todas as métricas necessárias no relatório\n2. Integrar Plane API + scripts Python\n3. Publicar automaticamente via GitHub Actions toda sexta\n4. Validar saída por 2 semanas com dados reais\n5. Deprecar processo manual",
      "expected_result": "Eliminar 2-3h semanais de trabalho manual e garantir consistência e rastreabilidade das métricas.",
      "metric_label": "Horas gastas no relatório / semana",
      "metric_before": 2.5,
      "metric_after": 0.1,
      "metric_unit": "h"
    },
    {
      "id": "imp-003",
      "title": "Criar SLA de resposta ao cliente por canal",
      "area": "CS",
      "status": "a_melhorar",
      "priority": "alta",
      "created_at": "2026-04-07",
      "updated_at": "2026-04-07",
      "description": "Não existe um SLA definido por canal (WhatsApp, e-mail, chat) — o time responde sem padrão de prazo, gerando insatisfação e dificuldade de mensurar qualidade de atendimento.",
      "action_plan": "1. Levantar tempo médio de resposta atual por canal\n2. Definir SLAs realistas com o time de CS\n3. Configurar alertas de SLA no sistema de atendimento\n4. Criar dashboard de acompanhamento\n5. Revisar SLAs após 30 dias",
      "expected_result": "Reduzir tempo médio de resposta em 40% e aumentar CSAT de 72% para 85% em 60 dias.",
      "metric_label": "CSAT",
      "metric_before": 72,
      "metric_after": null,
      "metric_unit": "%"
    },
    {
      "id": "imp-004",
      "title": "Centralizar documentação de processos internos",
      "area": "Gestão",
      "status": "finalizado",
      "priority": "media",
      "created_at": "2026-01-15",
      "updated_at": "2026-03-20",
      "description": "Processos críticos estavam documentados em arquivos locais, e-mails e memória das pessoas — gerando retrabalho, erros no onboarding e dependência de pessoas específicas.",
      "action_plan": "1. Auditar todos os processos existentes\n2. Escolher ferramenta (Notion)\n3. Documentar os 10 processos mais críticos\n4. Criar template padrão de documentação\n5. Integrar ao onboarding de novos colaboradores",
      "expected_result": "Reduzir tempo de onboarding de novos colaboradores e eliminar erros causados por falta de documentação.",
      "metric_label": "Tempo médio de onboarding",
      "metric_before": 14,
      "metric_after": 7,
      "metric_unit": "d"
    }
  ]
}
$json$::jsonb
)
on conflict (id) do update
set payload = excluded.payload,
    updated_at = timezone('utc', now());

create or replace view public.v_week_index
with (security_invoker = on) as
select
  week_key as key,
  payload->>'week_label' as label,
  payload->>'week_range' as range,
  nullif(payload->'current'->>'rate', '')::numeric as rate,
  payload->>'generated_at' as generated_at
from public.operation_weeks;

alter table public.operation_weeks enable row level security;
alter table public.dashboard_improvements enable row level security;

drop policy if exists "operation_weeks_select_anon" on public.operation_weeks;
create policy "operation_weeks_select_anon"
on public.operation_weeks
for select
to anon
using (true);

drop policy if exists "dashboard_improvements_select_anon" on public.dashboard_improvements;
create policy "dashboard_improvements_select_anon"
on public.dashboard_improvements
for select
to anon
using (true);

grant select on public.operation_weeks to anon;
grant select on public.dashboard_improvements to anon;
grant select on public.v_week_index to anon;
