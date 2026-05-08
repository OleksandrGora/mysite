"""
Anonymizer — анонімізація/деанонімізація промптів перед відправкою в LLM.

Використовує:
- Regex: телефони, адреси, номери документів, email, URL, ФОП
- spaCy (xx_ent_wiki_sm): імена людей, організації

Типове використання:
    anonymizer = Anonymizer()
    anon_text = anonymizer.anonymize("Контакт: Іван Петренко, тел. +380501234567")
    restored = anonymizer.deanonymize(anon_text)  # повертає оригінал
    anonymizer.reset()  # між сесіями
"""

import re
import spacy
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Anonymizer:
    """
    Анонімізатор з двома етапами:
      1️⃣ Regex-патерни (телефони, адреси, email, URL, номери документів, ФОП)
      2️⃣ spaCy NER (PERSON, ORG)

    Кожен знайдений елемент замінюється на плейсхолдер [КАТЕГОРІЯ_N],
    а відповідність зберігається в _map для деанонімізації.
    """
    _map: Dict[str, str] = field(default_factory=dict)
    _counter: Dict[str, int] = field(default_factory=lambda: {
        "PERSON": 0,
        "ORG": 0,
        "PHONE": 0,
        "ADDRESS": 0,
        "DOC_NUM": 0,
        "EMAIL": 0,
        "URL": 0,
        "FOP": 0,
    })
    _nlp: object = field(default=None, init=False)

    # Регулярні вирази для анонімізації
    PATTERNS = [
        ("EMAIL", re.compile(
            r'\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b'
        )),
        ("URL", re.compile(
            r'https?://[^\s]+'
        )),
        ("PHONE", re.compile(
            r'\b(\+?38\s?)?(\(?\d{3}\)?[\s\-]?)\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b'
        )),
        ("DOC_NUM", re.compile(
            r'№\s?[А-ЯA-ZДС]{0,5}\d{6,}'
        )),
        ("FOP", re.compile(
            r'\bФОП\s[«"\'‹][^»"\'›]+[»"\'›]'
            r'|\bФОП\s+[А-ЯІЇЄA-Z][а-яіїєa-z]+\s[А-ЯІЇЄ]\.[А-ЯІЇЄ]\.'
            r'|\bФОП\s+[А-ЯІЇЄA-Z][а-яіїєa-z]+'
            r'(?:\s[А-ЯІЇЄA-Z][а-яіїєa-z]+)?'
            r'(?:\s[А-ЯІЇЄA-Z][а-яіїєa-z]+)?',
            re.UNICODE
        )),
        ("ADDRESS", re.compile(
            r'\b(?:вул\.|м\.|пров\.|просп\.|бульв\.|пл\.|провулок|проспект|бульвар|площа)\s'
            r'[А-ЯІЇЄа-яіїєA-Za-z\'″][А-ЯІЇЄа-яіїєA-Za-z.,\s\d\'\-″]{1,60}'
            r'(?:,\s*\d+[А-ЯA-Z]?(?:\s*/\s*\d+[А-ЯA-Z]?)?)?'
            r'(?:,\s*(?:поверх|пов\.|кв\.|офіс|буд\.|корп\.)\s*\d+[А-ЯA-Z]?)?',
            re.IGNORECASE
        )),
    ]

    def __post_init__(self):
        try:
            self._nlp = spacy.load("xx_ent_wiki_sm")
        except OSError:
            import subprocess
            import sys
            subprocess.check_call(
                [sys.executable, "-m", "spacy", "download", "xx_ent_wiki_sm"]
            )
            self._nlp = spacy.load("xx_ent_wiki_sm")

    def _placeholder(self, category: str, original: str) -> str:
        """Повертає плейсхолдер для оригінального тексту, зберігаючи маппінг."""
        # Перевірка, чи вже є маппінг для цього оригіналу
        for placeholder, orig in self._map.items():
            if orig == original:
                return placeholder
        self._counter[category] += 1
        placeholder = f"[{category}_{self._counter[category]}]"
        self._map[placeholder] = original
        return placeholder

    def anonymize(self, text: str) -> str:
        """
        Анонімізує текст:
          1️⃣ Замінює всі regex-патерни на плейсхолдери
          2️⃣ Замінює PERSON/ORG через spaCy NER

        Args:
            text: Оригінальний текст

        Returns:
            Анонімізований текст з плейсхолдерами
        """
        # 1. Regex-патерни
        for category, pattern in self.PATTERNS:
            def replacer(m, cat=category):
                return self._placeholder(cat, m.group(0))
            text = pattern.sub(replacer, text)

        # 2. spaCy NER — імена та організації
        # Спочатку захищаємо вже наявні плейсхолдери, щоб spaCy не чіпала їх
        protected = {}
        def _protect(m):
            token = f"\x00PROTECT_{len(protected)}\x00"
            protected[token] = m.group(0)
            return token
        text_protected = re.sub(r'\[[A-Z_]+\d+\]', _protect, text)

        doc = self._nlp(text_protected)
        entities = sorted(doc.ents, key=lambda e: e.start_char, reverse=True)
        for ent in entities:
            ent_text = text_protected[ent.start_char:ent.end_char]
            # Пропускаємо, якщо це захищений плейсхолдер
            if ent_text.startswith('\x00PROTECT_'):
                continue
            if ent.label_ in ("PER", "PERSON"):
                placeholder = self._placeholder("PERSON", ent_text)
                text_protected = text_protected[:ent.start_char] + placeholder + text_protected[ent.end_char:]
            elif ent.label_ in ("ORG",):
                placeholder = self._placeholder("ORG", ent_text)
                text_protected = text_protected[:ent.start_char] + placeholder + text_protected[ent.end_char:]

        # Відновлюємо захищені плейсхолдери
        for token, original in protected.items():
            text_protected = text_protected.replace(token, original)

        return text_protected

    def deanonymize(self, text: str) -> str:
        """
        Відновлює оригінальний текст з плейсхолдерів.

        Args:
            text: Текст з плейсхолдерами

        Returns:
            Відновлений текст
        """
        for placeholder, original in self._map.items():
            text = text.replace(placeholder, original)
        return text

    def get_map(self) -> Dict[str, str]:
        """
        Повертає поточну карту відповідностей.
        Корисно для логування або розширеного контролю.
        """
        return dict(self._map)

    def reset(self):
        """Очищає карту замін — викликати між різними сесіями/користувачами."""
        self._map.clear()
        for key in self._counter:
            self._counter[key] = 0

# Модульний singleton — один екземпляр на весь застосунок
_default_anonymizer: Anonymizer = None


def get_anonymizer() -> Anonymizer:
    """
    Повертає глобальний екземпляр Anonymizer.

    Використовується в інтеграційних точках (pipeline, generator),
    щоб усі компоненти працювали з однією мапою замін.
    """
    global _default_anonymizer
    if _default_anonymizer is None:
        _default_anonymizer = Anonymizer()
    return _default_anonymizer
