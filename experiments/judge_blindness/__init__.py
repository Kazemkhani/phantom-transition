"""Judge-blindness experiment: can a per-turn language-model judge see a phantom transition?

Modules:
    bank          seeded template bank for synthetic outbound qualification calls
    generate      deterministic generator of sessions (transcript plus hidden state trace)
    postcondition the one-line state post-condition
    judge         API-agnostic LLM-judge runner with emit and ingest modes
    analyse       tables and summary
"""
