"""Busca de referências: BM25 puro-Python sobre nomes, categoria, cor, tags e legendas.

Consultas em português são traduzidas por um dicionário de sinônimos de jogos; palavras como "inimigo",
"chefe" ou "armadura" viram dicas de categoria (bônus no ranking, não filtro).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_STOP = {"a", "an", "the", "of", "with", "and", "in", "on", "for", "to", "um", "uma", "de", "do", "da", "dos", "das",
         "com", "e", "o", "os", "as", "em", "no", "na", "para", "none", "png"}

SYNONYMS: dict[str, list[str]] = {
    "espada": ["sword", "blade"], "lamina": ["blade"], "cajado": ["staff"], "varinha": ["wand", "rod"],
    "arco": ["bow"], "besta": ["crossbow"], "flecha": ["arrow"], "machado": ["axe"], "picareta": ["pickaxe"],
    "martelo": ["hammer"], "lanca": ["spear", "lance"], "escudo": ["shield"], "adaga": ["dagger", "knife"],
    "faca": ["knife"], "chicote": ["whip"], "foice": ["scythe", "sickle"], "pistola": ["pistol", "gun"],
    "arma": ["weapon", "gun"], "rifle": ["rifle"], "bala": ["bullet"], "bomba": ["bomb"], "granada": ["grenade"],
    "pocao": ["potion", "flask"], "frasco": ["flask", "bottle"], "livro": ["book", "tome"], "tomo": ["tome", "book"],
    "anel": ["ring"], "colar": ["necklace", "amulet"], "amuleto": ["amulet", "charm"], "bota": ["boots"],
    "botas": ["boots"], "asas": ["wings"], "asa": ["wings"], "capacete": ["helmet", "head"], "elmo": ["helmet"],
    "armadura": ["armor", "breastplate", "body"], "peitoral": ["breastplate"], "calca": ["leggings", "greaves"],
    "grevas": ["greaves"], "capa": ["cape", "cloak"], "manto": ["robe", "cloak"], "coroa": ["crown"],
    "moeda": ["coin"], "gema": ["gem"], "joia": ["gem", "jewel"], "cristal": ["crystal"], "minerio": ["ore"],
    "barra": ["bar"], "lingote": ["bar"], "bau": ["chest"], "chave": ["key"], "tocha": ["torch"],
    "lampada": ["lamp", "lantern"], "lanterna": ["lantern"], "vela": ["candle"], "porta": ["door"],
    "mesa": ["table"], "cadeira": ["chair"], "cama": ["bed"], "estatua": ["statue"], "bloco": ["block", "tile"],
    "parede": ["wall"], "arvore": ["tree"], "folha": ["leaf"], "flor": ["flower"], "cogumelo": ["mushroom"],
    "planta": ["plant"], "erva": ["herb"], "semente": ["seed"], "fogo": ["fire", "flame", "fiery"],
    "chama": ["flame", "fire"], "gelo": ["ice", "frost", "frozen"], "neve": ["snow", "ice"], "agua": ["water"],
    "raio": ["lightning", "thunder"], "eletrico": ["electric", "lightning"], "veneno": ["poison", "venom", "toxic"],
    "sombra": ["shadow", "dark"], "sombrio": ["dark", "shadow"], "luz": ["light", "holy"], "sagrado": ["holy", "hallowed"],
    "sangue": ["blood", "crimson"], "carmesim": ["crimson"], "corrupcao": ["corruption", "corrupt", "vile"],
    "magia": ["magic", "magical", "spell"], "magico": ["magic", "magical", "enchanted"], "encantado": ["enchanted"],
    "estrela": ["star"], "lua": ["moon", "lunar"], "sol": ["sun", "solar"], "ceu": ["sky"], "nuvem": ["cloud"],
    "inferno": ["hell", "underworld", "molten"], "lava": ["lava", "magma", "molten"], "selva": ["jungle"],
    "deserto": ["desert", "sand"], "areia": ["sand"], "oceano": ["ocean", "sea"], "mar": ["sea", "ocean"],
    "caverna": ["cave", "cavern"], "mina": ["mine"], "dourado": ["gold", "golden"], "ouro": ["gold"],
    "prata": ["silver"], "cobre": ["copper"], "ferro": ["iron"], "madeira": ["wood", "wooden"],
    "pedra": ["stone"], "osso": ["bone"], "zumbi": ["zombie"], "esqueleto": ["skeleton"], "olho": ["eye"],
    "dragao": ["dragon", "wyvern"], "morcego": ["bat"], "aranha": ["spider"], "verme": ["worm"],
    "minhoca": ["worm"], "abelha": ["bee"], "peixe": ["fish"], "passaro": ["bird"], "lobo": ["wolf"],
    "fantasma": ["ghost", "wraith"], "demonio": ["demon"], "anjo": ["angel"], "golem": ["golem"],
    "mago": ["wizard", "mage"], "bruxa": ["witch"], "pirata": ["pirate"], "robo": ["robot", "mechanical"],
    "mecanico": ["mechanical"], "slime": ["slime"], "geleia": ["slime", "gel"], "fada": ["fairy", "pixie"],
    "mascote": ["pet"], "bichinho": ["pet"], "montaria": ["mount"], "gancho": ["hook", "grappling"],
    "vara": ["rod"], "pesca": ["fishing"], "anzol": ["hook"], "isca": ["bait"], "comida": ["food"],
    "fruta": ["fruit"], "cogumelos": ["mushroom"], "tiro": ["shot", "bullet"], "explosao": ["explosion", "explosive"],
    "brilho": ["glow", "sparkle"], "brilhante": ["glowing", "shiny"], "azul": ["blue"], "vermelho": ["red"],
    "verde": ["green"], "amarelo": ["yellow"], "roxo": ["purple"], "rosa": ["pink"], "laranja": ["orange"],
    "branco": ["white"], "preto": ["black"], "cinza": ["gray"], "marrom": ["brown"],
    "cura": ["healing", "heal", "health", "life", "restoration"], "curar": ["healing", "heal"], "vida": ["life", "health"],
    "gigante": ["giant", "huge", "king", "big"], "grande": ["big", "giant", "large"], "pequeno": ["small", "mini", "tiny"],
    "bola": ["ball", "orb", "sphere"], "esfera": ["sphere", "orb", "ball"], "energia": ["energy", "magic"],
    "rastro": ["trail"], "projetil": ["projectile", "bolt"], "disparo": ["shot", "bolt", "blast"], "feixe": ["beam", "laser"],
    "laser": ["laser", "beam"], "roxa": ["purple"], "vermelha": ["red"], "amarela": ["yellow"], "branca": ["white"],
    "preta": ["black"], "dourada": ["gold", "golden"], "prateado": ["silver"], "prateada": ["silver"], "verdes": ["green"],
    "azuis": ["blue"], "venenoso": ["poison", "toxic"], "venenosa": ["poison", "toxic"], "bolhas": ["bubbles", "bubble"],
    "bolha": ["bubble"], "rachaduras": ["cracks", "cracked"], "rachado": ["cracked"], "topo": ["top"], "ponta": ["tip"],
    "cabo": ["handle", "hilt"], "lamina": ["blade"], "brilho": ["glow", "glowing"], "brilhante": ["glowing", "shiny"],
    "flamejante": ["flaming", "fiery"], "congelado": ["frozen", "ice"], "eletrica": ["electric", "lightning"],
    "rei": ["king"], "rainha": ["queen"], "chefao": ["boss", "king"], "verme": ["worm", "eater", "devourer"],
}

PT_WORDS = set(SYNONYMS)  # chaves em português (antes de mesclar as famílias em inglês)

# famílias em inglês (os nomes dos catálogos variam: Fiery, Flaming, Molten, Hellfire...)
_EN_FAMILIES = [
    ["fire", "fiery", "flame", "flaming", "flamelash", "molten", "hellfire", "inferno", "burning", "blaze", "lava", "magma"],
    ["ice", "icy", "frost", "frozen", "snow", "blizzard", "glacial", "cryo"],
    ["magic", "magical", "arcane", "mana", "enchanted", "spell", "mystic"],
    ["staff", "wand", "rod", "scepter", "sceptre"],
    ["sword", "blade", "saber", "sabre", "greatsword", "broadsword", "shortsword", "katana", "cutlass"],
    ["bow", "longbow", "shortbow", "shotbow", "repeater"],
    ["gun", "pistol", "rifle", "shotgun", "blaster", "revolver", "musket", "minishark"],
    ["shield", "buckler"], ["helmet", "helm", "head", "mask", "hood", "hat"],
    ["poison", "venom", "toxic", "acid"], ["shadow", "dark", "darkness", "void"],
    ["holy", "hallowed", "light", "angel", "divine"], ["blood", "crimson", "bloody", "vampire"],
    ["lightning", "thunder", "storm", "electric", "shock"], ["water", "aqua", "ocean", "sea", "tidal"],
    ["crystal", "gem", "jewel", "shard"], ["bat", "bats"], ["dragon", "wyvern", "drake"],
    ["skull", "bone", "skeleton"], ["star", "stellar", "astral", "cosmic"], ["moon", "lunar"], ["sun", "solar"],
]
for fam in _EN_FAMILIES:
    for w in fam:
        SYNONYMS.setdefault(w, [])
        SYNONYMS[w] = list(dict.fromkeys(SYNONYMS[w] + [x for x in fam if x != w]))
for pt, ens in list(SYNONYMS.items()):  # palavras pt herdam as famílias das traduções
    for en in list(ens):
        for fam in _EN_FAMILIES:
            if en in fam:
                SYNONYMS[pt] = list(dict.fromkeys(SYNONYMS[pt] + fam))

# palavras da consulta → categorias favorecidas (prefixos de categoria, comparados em minúsculas)
CATEGORY_HINTS: dict[str, list[str]] = {
    "enemy": ["npc"], "inimigo": ["npc"], "monster": ["npc"], "monstro": ["npc"], "boss": ["npc", "npc_head_boss"],
    "chefe": ["npc", "npc_head_boss"], "creature": ["npc"], "criatura": ["npc"], "npc": ["npc", "townnpcs"],
    "projectile": ["projectile"], "projetil": ["projectile"], "missile": ["projectile"],
    "armor": ["armor"], "armadura": ["armor"], "helmet": ["armor_head"], "capacete": ["armor_head"],
    "tile": ["tiles", "tile"], "bloco": ["tiles", "tile"], "wall": ["wall"], "parede": ["wall"],
    "background": ["background", "backgrounds"], "fundo": ["background", "backgrounds"], "cenario": ["background", "tiles"],
    "buff": ["buff"], "icon": ["buff", "item"], "icone": ["buff", "item"], "wings": ["wings"], "asas": ["wings"],
    "accessory": ["acc"], "acessorio": ["acc"], "gore": ["gore"], "ui": ["ui"], "interface": ["ui"],
    "item": ["item"], "weapon": ["item"], "slime": ["npc"], "zombie": ["npc"], "skeleton": ["npc"], "bat": ["npc"],
    "worm": ["npc"], "demon": ["npc"], "ghost": ["npc"], "dragon": ["npc"], "spider": ["npc"], "goblin": ["npc"],
    "eye": ["npc"], "olho": ["npc"], "king": ["npc"], "queen": ["npc"], "giant": ["npc"], "creature": ["npc"], "arma": ["item"], "sword": ["item"], "staff": ["item"], "potion": ["item"],
}


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


_GLUED = re.compile(r"(?<=[a-z])(of|the|and)(?=[A-Z])")


def split_name(name: str) -> list[str]:
    """'FieryGreatsword_2' → ['fiery', 'greatsword']; 'EyeofCthulhu' → ['eye', 'of', 'cthulhu']."""
    parts = re.split(r"[\s_\-./]+", _CAMEL.sub(" ", _GLUED.sub(r" \1 ", name)))
    return [p.lower() for p in parts if p and not p.isdigit()]


# substantivos que aparecem colados em nomes compostos (Greatsword, Shortbow, Hellwing, PinkSlime...)
KIND_SUFFIXES = ("sword", "blade", "saber", "staff", "wand", "rod", "bow", "axe", "hammer", "pickaxe", "pick", "spear",
                 "lance", "gun", "blaster", "cannon", "launcher", "shield", "helmet", "mask", "wings", "wing", "boots",
                 "slime", "eye", "bat", "worm", "fish", "potion", "flask", "tome", "book", "ring", "amulet", "chest",
                 "ore", "bar", "brick", "block", "wall", "torch", "lamp", "lantern", "crystal", "gem", "orb",
                 "bomb", "arrow", "bullet", "knife", "dagger", "whip", "yoyo", "claw", "fist", "skull", "bone")


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", norm(" ".join(split_name(text)) if text else "")):
        if w in _STOP or w.isdigit():
            continue
        out.append(w)
        for suf in KIND_SUFFIXES:
            if len(w) > len(suf) + 2 and w.endswith(suf):
                out.append(suf)
                break
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            out.append(w[:-1])  # plural simples
    return out


def expand_query(query: str) -> tuple[list[list[str]], set[str]]:
    """(grupos de termos — um por conceito da consulta, com sinônimos —, categorias favorecidas)."""
    groups, cats = [], set()
    for w in re.findall(r"[a-z0-9]+", norm(query)):
        if w in _STOP or w.isdigit():
            continue
        g = [w, *SYNONYMS.get(w, [])]
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            g += [w[:-1], *SYNONYMS.get(w[:-1], [])]
        groups.append(list(dict.fromkeys(g)))
        cats.update(CATEGORY_HINTS.get(w, []))
        for syn in g[1:]:
            cats.update(CATEGORY_HINTS.get(syn, []))
    return groups, cats


def doc_tokens(e: dict[str, Any]) -> list[str]:
    toks = tokens(e.get("name") or "") * 3  # nome pesa mais
    toks += tokens(e.get("category") or "")
    toks += tokens(e.get("colour") or "")
    toks += tokens(" ".join(e.get("tags") or []))
    toks += tokens(e.get("caption") or "")
    return toks


class SearchIndex:
    def __init__(self, entries: list[dict[str, Any]], k1: float = 1.4, b: float = 0.6):
        self.entries = entries
        self.docs = [Counter(doc_tokens(e)) for e in entries]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(entries)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, d in enumerate(self.docs):
            for t in d:
                self.postings[t].append(i)

    def _score(self, i: int, terms: list[str]) -> float:
        d, ln, s = self.docs[i], self.lens[i], 0.0
        for t in set(terms):
            f = d.get(t, 0)
            if f:
                s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
        return s

    def search(self, query: str, k: int = 8, *, categories: list[str] | None = None, exclude: set[str] | None = None,
               pinned: set[str] | None = None, diverse: bool = True) -> list[dict[str, Any]]:
        groups, hinted = expand_query(query)
        groups = [g for g in groups if any(t in self.postings for t in g)]  # palavras sem nenhuma ref não contam
        terms = [t for g in groups for t in g]
        allowed = [c.lower() for c in categories] if categories else None
        cand: set[int] = set()
        for t in terms:
            cand.update(self.postings.get(t, []))
        scored = []
        for i in cand:
            e = self.entries[i]
            cat = (e.get("category") or "").lower()
            if allowed and not any(cat.startswith(a) for a in allowed):
                continue
            if exclude and e["id"] in exclude:
                continue
            s = self._score(i, terms)
            if len(groups) > 1:  # cobre todos os conceitos da consulta ("fire" E "sword")?
                d = self.docs[i]
                cov = sum(any(d.get(t) for t in g) for g in groups) / len(groups)
                s *= 0.15 + 0.85 * cov ** 2
            if hinted and any(cat.startswith(h) for h in hinted):
                s *= 1.6
            if pinned and e["id"] in pinned:
                s *= 1.3
            scored.append((s, i))
        scored.sort(key=lambda x: -x[0])
        out, seen = [], set()
        for s, i in scored:
            e = self.entries[i]
            key = " ".join(tokens(e.get("name") or e["id"]))
            if diverse and key and key in seen:
                continue
            seen.add(key)
            out.append({**e, "score": round(s, 3)})
            if len(out) >= k:
                break
        if not out and pinned:  # sem casamento: usa as fixadas como estilo geral
            out = [{**e, "score": 0.0} for e in self.entries if e["id"] in pinned][:k]
        return out
