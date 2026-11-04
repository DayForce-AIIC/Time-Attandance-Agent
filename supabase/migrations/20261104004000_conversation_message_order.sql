-- Keep message order explicit even when both messages in one exchange share
-- the same transaction timestamp.

alter table public.messages add column sequence_number bigint;

with ranked_messages as (
  select id,
         row_number() over (
           partition by conversation_id order by created_at, id
         ) as sequence_number
  from public.messages
)
update public.messages as messages
set sequence_number = ranked_messages.sequence_number
from ranked_messages
where messages.id = ranked_messages.id;

alter table public.messages alter column sequence_number set not null;

create unique index messages_conversation_sequence_idx
  on public.messages(conversation_id, sequence_number);
