"""Guardrails for the interview hot path.

Design note: the fast checks here are deliberately rule-based rather than an LLM call.
An extra LLM round trip before the main completion adds ~500-1500ms of dead air after
the candidate stops speaking, which reads as a broken interview. Rules catch the blunt
cases in microseconds; the nuanced judgement runs in `review_turn` off the hot path and
only flags for HR review after the fact.
"""

import re
from dataclasses import dataclass, field

from app.services import modelark

# Attempts to make the interviewer drop its persona or leak its instructions.
_INJECTION = [
    r"ignore (all |any |your |the )?(previous|prior|above|earlier) (instruction|prompt|rule)",
    r"disregard (all |any |your |the )?(previous|prior|above|earlier)",
    r"(system|initial|original) prompt",
    r"you are (now|actually) (a|an) ",
    r"pretend (to be|you are)",
    r"act as (a|an) ",
    r"developer mode",
    r"jailbreak",
    r"repeat (everything |all )?(your|the) (instructions|rules|prompt)",
    r"what (are|were) your instructions",
]

# Requests the interviewer should not fulfil even though they are not attacks.
_OFF_TASK = [
    r"\b(write|generate|create) (me )?(a |an )?(poem|song|story|essay|code|script|program)\b",
    r"\b(solve|calculate|compute) (this|the following)\b",
    r"\btranslate (this|the following)\b",
    r"\bwhat('s| is) the weather\b",
    r"\btell me a joke\b",
]

# Things that must stop the interview rather than be deflected.
_ESCALATE = [
    r"\b(kill|hurt|harm) (myself|yourself|someone)\b",
    r"\bsuicide\b",
]

# Questions about hiring outcomes the AI must never answer - it does not decide these
# and a guessed answer creates legal exposure.
_OUT_OF_SCOPE = [
    r"\b(did|do) i (get|pass|fail)\b",
    r"\b(am i|was i) (hired|rejected|successful)\b",
    r"\bwhat('s| is) (my|the) (score|rating|result)\b",
    r"\bhow did i do\b",
    r"\b(what|how much) (is|does) the (salary|pay|compensation)\b",
    r"\bwill i get the job\b",
]

_DEFLECTIONS = {
    "off_task": {
        "en": "Let's keep our focus on the role and your experience. {redirect}",
        "id": "Mari kita tetap fokus pada posisi ini dan pengalaman Anda. {redirect}",
        "zh": "我们还是把重点放在这个职位和您的经历上。{redirect}",
    },
    "injection": {
        "en": "I'm here to run this interview, so let's stay with that. {redirect}",
        "id": "Saya di sini untuk memandu wawancara ini, jadi mari kita lanjutkan. {redirect}",
        "zh": "我在这里负责这次面试，我们继续吧。{redirect}",
    },
    "out_of_scope": {
        "en": (
            "That's a decision for the hiring team rather than me, and they'll follow up "
            "after the interview. {redirect}"
        ),
        "id": (
            "Keputusan itu ada pada tim rekrutmen, dan mereka akan menghubungi Anda "
            "setelah wawancara. {redirect}"
        ),
        "zh": "这个由招聘团队决定，面试结束后他们会与您联系。{redirect}",
    },
    "low_confidence": {
        "en": (
            "I don't have that detail to hand, and I'd rather not guess - the hiring team "
            "can cover it. {redirect}"
        ),
        "id": (
            "Saya tidak memiliki detail itu dan tidak ingin menebak - tim rekrutmen dapat "
            "menjelaskannya. {redirect}"
        ),
        "zh": "这个细节我手上没有，也不想猜测，招聘团队可以为您解答。{redirect}",
    },
}

_REDIRECT = {
    "en": "Back to my question:",
    "id": "Kembali ke pertanyaan saya:",
    "zh": "回到我的问题：",
}

# Retrieval below this normalised score means neither the job requirements nor the
# knowledge base actually covers the question, so the model would be freelancing.
# VERIFY: tuned for the old TF-IDF score. rag.py's local vector search rescales cosine
# similarity (see _normalize_cosine) to land back in roughly this range, but that rescale
# is an estimate - re-tune both together against real interview documents/questions.
LOW_CONFIDENCE_THRESHOLD = 0.28


@dataclass
class GuardrailDecision:
    # allow | deflect | escalate
    action: str = "allow"
    reason: str | None = None
    deflection: str | None = None
    matched: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action != "allow"


def _match(patterns: list[str], text: str) -> str | None:
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return pat
    return None


def _deflection_text(kind: str, language: str, next_question: str | None) -> str:
    lang = language if language in _REDIRECT else "en"
    template = _DEFLECTIONS[kind].get(lang, _DEFLECTIONS[kind]["en"])
    redirect = f"{_REDIRECT[lang]} {next_question}" if next_question else ""
    return template.format(redirect=redirect).strip()


def check_input(
    utterance: str, *, language: str = "en", next_question: str | None = None
) -> GuardrailDecision:
    """Fast pre-response check on what the candidate just said."""
    text = utterance.strip()
    if not text:
        return GuardrailDecision()

    if pat := _match(_ESCALATE, text):
        return GuardrailDecision(
            action="escalate",
            reason="safety",
            matched=[pat],
            deflection=(
                "I'm going to pause the interview here. Please reach out to someone you "
                "trust or a local support line - a member of our team will follow up."
            ),
        )

    for kind, patterns in (
        ("injection", _INJECTION),
        ("out_of_scope", _OUT_OF_SCOPE),
        ("off_task", _OFF_TASK),
    ):
        if pat := _match(patterns, text):
            return GuardrailDecision(
                action="deflect",
                reason=kind,
                matched=[pat],
                deflection=_deflection_text(kind, language, next_question),
            )

    return GuardrailDecision()


def check_retrieval(
    score: float, *, is_question: bool, language: str = "en", next_question: str | None = None
) -> GuardrailDecision:
    """Deflect candidate *questions* the documents do not cover. Only applies when the
    candidate asked something - a weak retrieval score on a normal answer is expected and
    must not interrupt the interview."""
    if not is_question or score >= LOW_CONFIDENCE_THRESHOLD:
        return GuardrailDecision()
    return GuardrailDecision(
        action="deflect",
        reason="low_confidence",
        deflection=_deflection_text("low_confidence", language, next_question),
    )


def looks_like_question(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    openers = (
        "what", "how", "why", "when", "where", "who", "which",
        "can you", "could you", "do you", "does the", "is there", "are there",
        "tell me about the",
    )
    return stripped.lower().startswith(openers)


async def review_turn(candidate_text: str, ai_text: str, guardrail_notes: str | None) -> dict:
    """Post-hoc LLM review. Runs after the response has already been streamed, so its
    latency is invisible to the candidate. Output is advisory - it flags turns for HR
    rather than changing what was said."""
    prompt = [
        {
            "role": "system",
            "content": (
                "You audit transcripts of AI-conducted job interviews. Return JSON with "
                "keys: on_topic (bool), concern (string or null), severity "
                "(none|low|medium|high). Flag anything where the interviewer gave hiring "
                "decisions, discussed compensation, made legal commitments, asked "
                "discriminatory questions, or drifted off the role.\n"
                f"Additional policy from HR: {guardrail_notes or 'none'}"
            ),
        },
        {
            "role": "user",
            "content": f"CANDIDATE: {candidate_text}\n\nINTERVIEWER: {ai_text}",
        },
    ]
    result = await modelark.chat_json(prompt)
    return result if isinstance(result, dict) else {}
