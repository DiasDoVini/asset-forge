"""Política de conteúdo por projeto.

- Projeto normal (padrão): prompts com conteúdo sexual explícito são recusados (habilite o modo +18 no projeto).
- Projeto +18 (`nsfw: true`, ligado só pelo usuário na UI/CLI com confirmação de maioridade): conteúdo adulto
  liberado. Qualquer referência a menores de idade é SEMPRE recusada nesses projetos — não há como desligar isso.
  Personagens devem ser adultos.

A checagem cobre todos os textos dos parâmetros (prompt, instruction, lyrics, tags, JSONs...) e o estilo do projeto.
"""
from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .project import Project


class ContentPolicyError(ValueError):
    pass


# letras parecidas de outros alfabetos (cirílico/grego) → latino
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "к": "k", "м": "m",
    "н": "h", "т": "t", "в": "b", "ѕ": "s", "ԁ": "d", "ɡ": "g", "ӏ": "l", "α": "a", "ε": "e", "ο": "o", "ρ": "p",
    "τ": "t", "υ": "u", "ι": "i", "κ": "k", "ν": "v", "χ": "x",
})
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "|": "l"})


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().translate(_HOMOGLYPHS))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.translate(_HOMOGLYPHS)
    t = re.sub(r"[_\-]+", " ", t)
    return t


def _collapse_spaced(t: str) -> str:
    """'t e e n' / 'l.o.l.i' → 'teen' / 'loli' (3+ letras isoladas seguidas)."""
    return re.sub(r"\b(?:[a-z][\s.*]+){2,}[a-z]\b", lambda m: re.sub(r"[\s.*]+", "", m.group(0)), t)


def _variants(text: str) -> list[str]:
    """Texto normalizado + versões sem leetspeak e com letras espaçadas juntadas."""
    t = _norm(text)
    out = [t, _collapse_spaced(t)]
    # leetspeak só dentro de palavras com letras (preserva idades como "15 years")
    deleet = re.sub(r"\b(?=\w*[a-z])[\w@$!|]+\b", lambda m: m.group(0).translate(_LEET), t)
    out += [deleet, _collapse_spaced(deleet)]
    return list(dict.fromkeys(out))


# termos que indicam menores de idade (en/pt/es/jp romanizado); casam por palavra inteira após normalização
_MINOR_WORDS = [
    "child", "children", "childlike", "kid", "kids", "kiddo", "minor", "minors", "underage", "under age", "teen", "teens",
    "teenage", "teenager", "teenagers", "preteen", "pre teen", "tween", "loli", "lolis", "lolita", "lolicon", "shota",
    "shotacon", "toddler", "toddlers", "infant", "infants", "baby", "babies", "newborn", "schoolgirl", "schoolgirls",
    "schoolboy", "schoolboys", "school girl", "school boy", "elementary school", "middle school", "junior high",
    "young girl", "young boy", "little girl", "little boy", "little girls", "little boys", "girlchild", "boychild",
    "prepubescent", "pubescent", "jailbait", "cub", "cunny", "petite child", "small child",
    "crianca", "criancas", "infantil", "infantis", "menina", "meninas", "menino", "meninos", "garotinha", "garotinho",
    "garotinhas", "garotinhos", "adolescente", "adolescentes", "pre adolescente", "novinha", "novinhas", "novinho",
    "colegial", "colegiais", "bebe", "bebes", "menor de idade", "menores de idade", "mirim",
    "nina", "nino", "ninas", "ninos", "jovencita", "jovencito",
    "shoujo", "shounen", "chibi loli", "jk", "joshi kosei", "joshikosei", "gakuen",
    "underaged", "under aged", "teenaged", "teeny", "teenie", "adolescent", "adolescents", "youngster", "youngsters",
    "juvenile", "juveniles", "kiddie", "kiddies", "jail bait", "high school", "highschool", "high schooler",
    "highschooler", "grade school", "primary school", "ensino medio", "ensino fundamental", "escola primaria",
    "secundaria", "colegio", "menor de 18", "menores de 18", "under 18", "under eighteen", "below 18", "not 18",
]
_MINOR_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(_MINOR_WORDS, key=len, reverse=True)) + r")\b")
# idades explícitas abaixo de 18: "15 years old", "15yo", "15 y/o", "15 anos", "age 12", "aged 9"
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
    "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16,
    "dezessete": 17, "cuatro": 4, "siete": 7, "ocho": 8, "diez": 10, "doce": 12, "trece": 13,
    "dieciseis": 16, "diecisiete": 17,
}
_N = r"(\d{1,2}|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")"
_AGE_RE = re.compile(
    rf"\b{_N}\s*(?:years?\s*old|yrs?\s*old|year\s*old|y\s*[./]?\s*o\b\.?|yo|anos|ano|anos de idade|anos)\b"
    rf"|\bage[ds]?\s*(?:of\s*)?{_N}\b|\bidade\s*(?:de\s*)?{_N}\b|\b{_N}\s*(?:yo|y/o)\b")

# bloqueados em QUALQUER projeto (o jogo pode ter violência; isto não)
_ALWAYS_BLOCKED: dict[str, list[str]] = {
    "automutilação/suicídio": [
        "self harm", "selfharm", "self harming", "self injury", "self injuring", "self mutilation", "self mutilating",
        "cutting herself", "cutting himself", "cutting themselves", "cutting myself", "cuts herself", "cuts himself",
        "slit wrists", "slit her wrists", "slit his wrists", "slitting wrists", "wrist cutting", "cut wrists",
        "slashed wrists", "suicide", "suicidal", "kill herself", "kill himself", "kill themselves", "killing herself",
        "killing himself", "hanged herself", "hanged himself", "hanging herself", "hanging himself",
        "automutilacao", "auto mutilacao", "autolesao", "auto lesao", "se cortando", "se corta", "cortar os pulsos",
        "cortando os pulsos", "pulsos cortados", "suicidio", "suicida", "se matando", "se matar", "se enforcando",
        "se enforcou", "autolesion", "suicidarse",
    ],
    "violência sexual": [
        "rape", "raped", "raping", "rapist", "non consensual", "nonconsensual", "noncon", "forced sex",
        "sexual assault", "sexually assaulted", "molest", "molested", "molesting", "molestation", "drugged and",
        "unconscious sex", "sleeping sex", "estupro", "estuprada", "estuprado", "estuprando", "abuso sexual",
        "sexo forcado", "sem consentimento", "violacion", "violada",
    ],
    "zoofilia": [
        "bestiality", "zoophilia", "zoophile", "zoofilia", "bestialidade", "sex with animal", "sex with a dog",
        "sex with a horse", "animal sex", "sexo com animal", "sexo com cachorro", "sexo com cavalo",
    ],
}
_ALWAYS_RE = {cat: re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True)) + r")\b")
              for cat, ws in _ALWAYS_BLOCKED.items()}

_EXPLICIT_WORDS = [
    "nsfw", "nude", "nudes", "naked", "nudity", "sex", "sexual", "sexy nude", "porn", "porno", "pornographic",
    "explicit", "hentai", "ecchi", "genital", "genitals", "penis", "vagina", "vulva", "nipple", "nipples", "topless",
    "bottomless", "erotic", "erotica", "xxx", "intercourse", "orgasm", "cum", "masturbate", "masturbating", "masturbation", "fellatio", "blowjob",
    "nu", "nua", "nus", "nuas", "pelado", "pelada", "pelados", "peladas", "sexo", "sexual", "porno", "pornografia",
    "pornografico", "erotico", "erotica", "explicito", "explicita", "seios nus", "mamilos", "genitais", "transa",
]
_EXPLICIT_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(_EXPLICIT_WORDS, key=len, reverse=True)) + r")\b")


def _texts(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in ("input_asset", "mask", "reference_assets", "asset", "texture", "code"):
                continue  # refs e código não são texto descritivo
            yield from _texts(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _texts(v)


def _age(token: str) -> int:
    return int(token) if token.isdigit() else _NUM_WORDS.get(token, 99)


def find_minor_reference(text: str) -> str | None:
    for t in _variants(text):
        m = _MINOR_RE.search(t)
        if m:
            return m.group(1)
        for m in _AGE_RE.finditer(t):
            n = next((g for g in m.groups() if g), None)
            if n is not None and _age(n) < 18:
                return m.group(0).strip()
    return None


def find_always_blocked(text: str) -> tuple[str, str] | None:
    """(categoria, termo) se o texto pedir algo proibido em qualquer projeto."""
    for t in _variants(text):
        for cat, rx in _ALWAYS_RE.items():
            m = rx.search(t)
            if m:
                return cat, m.group(1)
    return None


def find_explicit(text: str) -> str | None:
    for t in _variants(text):
        m = _EXPLICIT_RE.search(t)
        if m:
            return m.group(1)
    return None


def is_nsfw(project: "Project | None") -> bool:
    return bool(project is not None and getattr(project.config, "nsfw", False))


def check_texts(texts: Iterable[str], nsfw: bool) -> None:
    for t in texts:
        if not t:
            continue
        hit2 = find_always_blocked(t)
        if hit2:
            raise ContentPolicyError(f"recusado: conteúdo de {hit2[0]} não é permitido (encontrado: '{hit2[1]}').")
        if nsfw:
            hit = find_minor_reference(t)
            if hit:
                raise ContentPolicyError(
                    f"recusado: projetos +18 não aceitam nenhuma referência a menores de idade (encontrado: '{hit}'). "
                    "Todos os personagens devem ser claramente adultos.")
        else:
            hit = find_explicit(t)
            if hit:
                raise ContentPolicyError(
                    f"conteúdo adulto ('{hit}') não é permitido neste projeto. Para conteúdo +18 habilite o modo +18 "
                    "nas configurações do projeto.")


def check_params(project: "Project", params: dict[str, Any]) -> None:
    nsfw = is_nsfw(project)
    texts = list(_texts(params))
    if nsfw:  # o estilo do projeto também entra nos prompts
        st = project.config.style
        texts += [st.prompt, st.negative, st.system_prompt]
    check_texts(texts, nsfw)


ADULT_SYSTEM_RULE = ("Hard rule: every character is an adult (18+), described with clearly adult features. Never "
                     "describe, imply or depict minors, childlike bodies, school settings for sexual content, or real "
                     "identifiable people in sexual content.")
