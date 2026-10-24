"""Coding: small functions and classes in Python and JavaScript, run against test cases the model never sees.

Each task states exactly what the function takes and returns, so a failure is a wrong program and not a different
reading of the request. One task is a bug to fix. The model answers with a code block; the reference solutions here
are the ones the tests were written against.
"""

from __future__ import annotations

from agentfit.arena.bank.kit import make
from agentfit.arena.model import Question

_PY_REPLY = "Rispondi con il solo codice, in un blocco ```python."
_JS_REPLY = "Rispondi con il solo codice, in un blocco ```javascript."


def py(code: str) -> str:
    return f"```python\n{code.strip()}\n```"


def js(code: str) -> str:
    return f"```javascript\n{code.strip()}\n```"


def _spec(entry: str, cases: list[dict], language: str = "python", **extra) -> dict:
    return {"type": "code", "language": language, "entry": entry, "cases": cases, "timeout": 10, **extra}


def _call(name: str, args: list, expected, **extra) -> dict:
    return {"call": name, "args": args, "expected": expected, **extra}


def build(seed: int) -> list[Question]:
    c = "coding"
    out: list[Question] = []

    out.append(make(c, 1, "Palindromo", "Scrivi in Python una funzione `is_palindrome(s)` che restituisce `True` se la stringa `s` è palindroma, ignorando maiuscole e minuscole e "
                    f"tutti i caratteri che non sono lettere o cifre, altrimenti `False`. {_PY_REPLY}",
                    _spec("is_palindrome", [_call("is_palindrome", ["A man, a plan, a canal: Panama"], True), _call("is_palindrome", ["race a car"], False), _call("is_palindrome", [""], True),
                                            _call("is_palindrome", ["Ai lati d'Italia"], True), _call("is_palindrome", ["0P"], False)]),
                    "is_palindrome(s) → bool, ignorando maiuscole e punteggiatura", py("def is_palindrome(s):\n    t = [ch.lower() for ch in s if ch.isalnum()]\n    return t == t[::-1]"),
                    wrong=[py("def is_palindrome(s):\n    return s == s[::-1]"), py("def is_palindrome(s):\n    s = s.lower()\n    return s == s[::-1]"),
                           py("def is_palindrome(s):\n    t = [ch for ch in s if ch.isalnum()]\n    return t == t[::-1]")], max_tokens=600, difficulty=1))

    out.append(make(c, 2, "Numeri romani", "Scrivi in Python una funzione `roman_to_int(s)` che converte un numero romano valido (da I a MMMCMXCIX) nel numero intero corrispondente. "
                    f"Per esempio `roman_to_int('IV')` vale 4. {_PY_REPLY}",
                    _spec("roman_to_int", [_call("roman_to_int", [s], v) for s, v in [("III", 3), ("IV", 4), ("IX", 9), ("LVIII", 58), ("MCMXCIV", 1994), ("MMXXVI", 2026), ("XLII", 42)]]),
                    "roman_to_int(s) → int", py("def roman_to_int(s):\n    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n    total = 0\n    for i, ch in enumerate(s):\n"
                                                "        if i + 1 < len(s) and values[ch] < values[s[i + 1]]:\n            total -= values[ch]\n        else:\n            total += values[ch]\n    return total"),
                    wrong=[py("def roman_to_int(s):\n    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n    return sum(values[ch] for ch in s)"),
                           py("def roman_to_int(s):\n    return len(s)")], max_tokens=700, difficulty=1))

    out.append(make(c, 3, "Intervalli sovrapposti", "Scrivi in Python una funzione `merge_intervals(intervals)` che riceve una lista di intervalli `[inizio, fine]` (non necessariamente ordinata) e restituisce "
                    f"la lista degli intervalli uniti: due intervalli che si sovrappongono o si toccano (la fine di uno uguale all'inizio dell'altro) diventano uno solo. La lista restituita è ordinata per inizio. {_PY_REPLY}",
                    _spec("merge_intervals", [_call("merge_intervals", [[[1, 3], [2, 6], [8, 10], [15, 18]]], [[1, 6], [8, 10], [15, 18]]), _call("merge_intervals", [[[1, 4], [4, 5]]], [[1, 5]]),
                                              _call("merge_intervals", [[]], []), _call("merge_intervals", [[[5, 6], [1, 3]]], [[1, 3], [5, 6]]), _call("merge_intervals", [[[1, 10], [2, 3]]], [[1, 10]])]),
                    "merge_intervals(intervals) → lista di [inizio, fine] ordinata",
                    py("def merge_intervals(intervals):\n    out = []\n    for start, end in sorted(intervals):\n        if out and start <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], end)\n        else:\n            out.append([start, end])\n    return out"),
                    wrong=[py("def merge_intervals(intervals):\n    out = []\n    for start, end in intervals:\n        if out and start <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], end)\n        else:\n            out.append([start, end])\n    return out"),
                           py("def merge_intervals(intervals):\n    out = []\n    for start, end in sorted(intervals):\n        if out and start < out[-1][1]:\n            out[-1][1] = max(out[-1][1], end)\n        else:\n            out.append([start, end])\n    return out"),
                           py("def merge_intervals(intervals):\n    out = []\n    for start, end in sorted(intervals):\n        if out and start <= out[-1][1]:\n            out[-1][1] = end\n        else:\n            out.append([start, end])\n    return out")],
                    max_tokens=900, difficulty=2))

    fizz = [("FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i)) for i in range(1, 16)]
    out.append(make(c, 4, "FizzBuzz", "Scrivi in Python una funzione `fizzbuzz(n)` che restituisce la lista di stringhe per i numeri da 1 a `n`: \"Fizz\" per i multipli di 3, \"Buzz\" per i multipli di 5, "
                    f"\"FizzBuzz\" per i multipli di entrambi, altrimenti il numero scritto come stringa. Con `n = 0` la lista è vuota. {_PY_REPLY}",
                    _spec("fizzbuzz", [_call("fizzbuzz", [15], fizz), _call("fizzbuzz", [0], []), _call("fizzbuzz", [5], ["1", "2", "Fizz", "4", "Buzz"]), _call("fizzbuzz", [1], ["1"])]),
                    "fizzbuzz(n) → lista di stringhe", py("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n        if i % 15 == 0:\n            out.append('FizzBuzz')\n        elif i % 3 == 0:\n            out.append('Fizz')\n"
                                                         "        elif i % 5 == 0:\n            out.append('Buzz')\n        else:\n            out.append(str(i))\n    return out"),
                    wrong=[py("def fizzbuzz(n):\n    out = []\n    for i in range(1, n + 1):\n        if i % 3 == 0:\n            out.append('Fizz')\n        elif i % 5 == 0:\n            out.append('Buzz')\n        elif i % 15 == 0:\n            out.append('FizzBuzz')\n        else:\n            out.append(str(i))\n    return out"),
                           py("def fizzbuzz(n):\n    return [str(i) for i in range(n)]")], max_tokens=700, difficulty=1))

    out.append(make(c, 5, "Frequenza delle parole", "Scrivi in Python una funzione `word_frequencies(text)` che restituisce un dizionario parola → numero di occorrenze. Le parole sono le sequenze di lettere e cifre "
                    f"(la punteggiatura le separa) e vanno contate in minuscolo. {_PY_REPLY}",
                    _spec("word_frequencies", [_call("word_frequencies", ["Il gatto e il cane. Il GATTO dorme!"], {"il": 3, "gatto": 2, "e": 1, "cane": 1, "dorme": 1}), _call("word_frequencies", [""], {}),
                                               _call("word_frequencies", ["Perché? Perché sì, perché!"], {"perché": 3, "sì": 1})]),
                    "word_frequencies(text) → dict", py("import re\n\n\ndef word_frequencies(text):\n    counts = {}\n    for word in re.findall(r'\\w+', text.lower()):\n        counts[word] = counts.get(word, 0) + 1\n    return counts"),
                    wrong=[py("def word_frequencies(text):\n    counts = {}\n    for word in text.lower().split():\n        counts[word] = counts.get(word, 0) + 1\n    return counts"),
                           py("import re\n\n\ndef word_frequencies(text):\n    counts = {}\n    for word in re.findall(r'\\w+', text):\n        counts[word] = counts.get(word, 0) + 1\n    return counts")], max_tokens=700, difficulty=2))

    out.append(make(c, 6, "Durata in secondi", "Scrivi in Python una funzione `parse_duration(s)` che converte una durata scritta come `1h30m`, `45s`, `2h`, `1h30m15s` o `90m` nel numero totale di secondi. "
                    f"Le unità sono ore (h), minuti (m) e secondi (s), in quest'ordine e ciascuna al più una volta. Una stringa vuota o non valida (`abc`, `1x`, `5m1h`) solleva `ValueError`. {_PY_REPLY}",
                    _spec("parse_duration", [_call("parse_duration", ["1h30m"], 5400), _call("parse_duration", ["45s"], 45), _call("parse_duration", ["2h"], 7200), _call("parse_duration", ["1h30m15s"], 5415),
                                             _call("parse_duration", ["90m"], 5400)] + [{"script": f"try:\n    parse_duration({bad!r})\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('no ValueError for {bad}')"} for bad in ("abc", "", "1x", "5m1h")]),
                    "parse_duration(s) → secondi, ValueError se non valida",
                    py("import re\n\n\ndef parse_duration(s):\n    m = re.fullmatch(r'(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?', s)\n    if not s or not m:\n        raise ValueError(s)\n    h, mi, se = (int(x or 0) for x in m.groups())\n    return h * 3600 + mi * 60 + se"),
                    wrong=[py("import re\n\n\ndef parse_duration(s):\n    total = 0\n    for n, unit in re.findall(r'(\\d+)([hms])', s):\n        total += int(n) * {'h': 3600, 'm': 60, 's': 1}[unit]\n    return total"),
                           py("def parse_duration(s):\n    return int(s.rstrip('s'))")], max_tokens=900, difficulty=2))

    out.append(make(c, 7, "Parentesi bilanciate", "Scrivi in Python una funzione `balanced(s)` che restituisce `True` se le parentesi `()`, `[]` e `{}` della stringa `s` sono bilanciate e annidate correttamente, "
                    f"ignorando ogni altro carattere. {_PY_REPLY}",
                    _spec("balanced", [_call("balanced", [s], v) for s, v in [("()[]{}", True), ("([{}])", True), ("(]", False), ("([)]", False), ("", True), ("a(b)c", True), ("((", False), (")(", False), ("{[()]}[]", True)]]),
                    "balanced(s) → bool", py("def balanced(s):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    stack = []\n    for ch in s:\n        if ch in '([{':\n            stack.append(ch)\n        elif ch in pairs:\n"
                                             "            if not stack or stack.pop() != pairs[ch]:\n                return False\n    return not stack"),
                    wrong=[py("def balanced(s):\n    return s.count('(') == s.count(')') and s.count('[') == s.count(']') and s.count('{') == s.count('}')"),
                           py("def balanced(s):\n    stack = []\n    for ch in s:\n        if ch in '([{':\n            stack.append(ch)\n        elif ch in ')]}':\n            stack.pop()\n    return not stack")], max_tokens=800, difficulty=2))

    out.append(make(c, 8, "Somma dei pari (JavaScript)", "Scrivi in JavaScript una funzione `sumEven(xs)` che restituisce la somma dei numeri pari (anche negativi) dell'array `xs`; "
                    f"per un array vuoto restituisce 0. {_JS_REPLY}",
                    _spec("sumEven", [_call("sumEven", [[1, 2, 3, 4]], 6), _call("sumEven", [[]], 0), _call("sumEven", [[-2, -4, 1]], -6), _call("sumEven", [[7, 9]], 0), _call("sumEven", [[10, 20, 30]], 60)], "javascript"),
                    "sumEven(xs) → numero", js("function sumEven(xs) {\n  return xs.filter((x) => x % 2 === 0).reduce((a, b) => a + b, 0);\n}"),
                    wrong=[js("function sumEven(xs) {\n  return xs.filter((x) => x % 2 === 1).reduce((a, b) => a + b, 0);\n}"), js("function sumEven(xs) {\n  return xs.filter((x) => x > 0 && x % 2 === 0).reduce((a, b) => a + b, 0);\n}"),
                           js("function sumEven(xs) {\n  return xs.reduce((a, b) => a + b, 0);\n}")], max_tokens=600, difficulty=1))

    out.append(make(c, 9, "Appiattire una lista", "Scrivi in Python una funzione `flatten(nested)` che restituisce una lista piatta con tutti gli elementi di una lista annidata a qualsiasi profondità, nello stesso ordine. "
                    f"Le stringhe sono elementi, non vanno scomposte. {_PY_REPLY}",
                    _spec("flatten", [_call("flatten", [[1, [2, [3, [4]], 5]]], [1, 2, 3, 4, 5]), _call("flatten", [[]], []), _call("flatten", [[[], [[]]]], []), _call("flatten", [["ab", ["cd"]]], ["ab", "cd"]), _call("flatten", [[1, 2, 3]], [1, 2, 3])]),
                    "flatten(nested) → lista", py("def flatten(nested):\n    out = []\n    for item in nested:\n        if isinstance(item, list):\n            out.extend(flatten(item))\n        else:\n            out.append(item)\n    return out"),
                    wrong=[py("def flatten(nested):\n    out = []\n    for item in nested:\n        if isinstance(item, list):\n            out.extend(item)\n        else:\n            out.append(item)\n    return out"),
                           py("def flatten(nested):\n    out = []\n    for item in nested:\n        try:\n            out.extend(flatten(item))\n        except TypeError:\n            out.append(item)\n    return out")], max_tokens=800, difficulty=2))

    lru_cases = [
        {"script": "c = LRUCache(2)\nc.put(1, 1)\nc.put(2, 2)\nassert c.get(1) == 1\nc.put(3, 3)\nassert c.get(2) == -1, 'key 2 should have been evicted'\nc.put(4, 4)\nassert c.get(1) == -1\nassert c.get(3) == 3\nassert c.get(4) == 4"},
        {"script": "c = LRUCache(1)\nc.put(1, 1)\nc.put(1, 2)\nassert c.get(1) == 2\nc.put(2, 3)\nassert c.get(1) == -1\nassert c.get(2) == 3"},
        {"script": "c = LRUCache(2)\nassert c.get(9) == -1\nc.put(1, 'a')\nc.put(2, 'b')\nc.put(1, 'c')\nc.put(3, 'd')\nassert c.get(2) == -1\nassert c.get(1) == 'c'"},
    ]
    out.append(make(c, 10, "Cache LRU", "Scrivi in Python una classe `LRUCache` con costruttore `LRUCache(capacity)`, il metodo `get(key)` che restituisce il valore oppure -1 se la chiave non c'è, e il metodo `put(key, value)` che inserisce "
                    f"o aggiorna un valore. Quando si supera la capacità viene eliminata la chiave usata meno di recente (sia `get` sia `put` contano come uso). {_PY_REPLY}", _spec("LRUCache", lru_cases),
                    "classe LRUCache(capacity) con get e put", py("from collections import OrderedDict\n\n\nclass LRUCache:\n    def __init__(self, capacity):\n        self.capacity = capacity\n        self.data = OrderedDict()\n\n    def get(self, key):\n"
                                                                   "        if key not in self.data:\n            return -1\n        self.data.move_to_end(key)\n        return self.data[key]\n\n    def put(self, key, value):\n        self.data[key] = value\n"
                                                                   "        self.data.move_to_end(key)\n        if len(self.data) > self.capacity:\n            self.data.popitem(last=False)"),
                    wrong=[py("from collections import OrderedDict\n\n\nclass LRUCache:\n    def __init__(self, capacity):\n        self.capacity = capacity\n        self.data = OrderedDict()\n\n    def get(self, key):\n        return self.data.get(key, -1)\n\n"
                              "    def put(self, key, value):\n        self.data[key] = value\n        if len(self.data) > self.capacity:\n            self.data.popitem(last=False)"),
                           py("class LRUCache:\n    def __init__(self, capacity):\n        self.capacity = capacity\n        self.data = {}\n\n    def get(self, key):\n        return self.data.get(key, -1)\n\n    def put(self, key, value):\n        self.data[key] = value")],
                    max_tokens=1200, difficulty=3))

    buggy = "def second_largest(nums):\n    nums.sort()\n    return nums[-2]"
    out.append(make(c, 11, "Correggere un bug", f"Questa funzione dovrebbe restituire il secondo valore più grande **distinto** di una lista di numeri, oppure `None` se i valori distinti sono meno di due. Non deve modificare la lista ricevuta.\n\n```python\n{buggy}\n```\n\n"
                    f"Correggila. {_PY_REPLY}", _spec("second_largest", [_call("second_largest", [[3, 1, 2]], 2), _call("second_largest", [[5, 5, 3]], 3), _call("second_largest", [[4]], None), _call("second_largest", [[2, 2]], None),
                                                                         _call("second_largest", [[]], None), _call("second_largest", [[1, 2, 3, 3, 2]], 2),
                                                                         {"script": "data = [3, 1, 2]\nsecond_largest(data)\nassert data == [3, 1, 2], 'the list was modified'"}]),
                    "second_largest(nums) → secondo valore distinto o None, senza modificare la lista",
                    py("def second_largest(nums):\n    distinct = sorted(set(nums), reverse=True)\n    return distinct[1] if len(distinct) >= 2 else None"),
                    wrong=[py("def second_largest(nums):\n    nums = sorted(nums)\n    return nums[-2]"), py("def second_largest(nums):\n    nums.sort()\n    return nums[-2] if len(nums) > 1 else None"),
                           py("def second_largest(nums):\n    distinct = sorted(set(nums))\n    return distinct[1] if len(distinct) >= 2 else None")], max_tokens=900, difficulty=2))

    out.append(make(c, 12, "Date nel testo", "Scrivi in Python una funzione `extract_dates(text)` che trova nel testo tutte le date scritte come `gg/mm/aaaa` (giorno e mese con una o due cifre, anno con quattro) e le restituisce "
                    f"come lista di stringhe ISO `aaaa-mm-gg`, nell'ordine in cui compaiono. Le date che non esistono nel calendario (come 31/02/2024) vengono ignorate. {_PY_REPLY}",
                    _spec("extract_dates", [_call("extract_dates", ["Dal 3/4/2026 al 15/04/2026"], ["2026-04-03", "2026-04-15"]), _call("extract_dates", ["31/02/2024 non esiste, 29/02/2024 sì"], ["2024-02-29"]),
                                            _call("extract_dates", ["nessuna data"], []), _call("extract_dates", ["29/02/2023 e 28/02/2023"], ["2023-02-28"]), _call("extract_dates", ["1/1/2000, 31/12/1999"], ["2000-01-01", "1999-12-31"])]),
                    "extract_dates(text) → lista di date ISO valide", py("import re\nfrom datetime import date\n\n\ndef extract_dates(text):\n    out = []\n    for d, m, y in re.findall(r'\\b(\\d{1,2})/(\\d{1,2})/(\\d{4})\\b', text):\n        try:\n"
                                                                         "            out.append(date(int(y), int(m), int(d)).isoformat())\n        except ValueError:\n            pass\n    return out"),
                    wrong=[py("import re\n\n\ndef extract_dates(text):\n    return [f'{y}-{m.zfill(2)}-{d.zfill(2)}' for d, m, y in re.findall(r'(\\d{1,2})/(\\d{1,2})/(\\d{4})', text)]"),
                           py("import re\nfrom datetime import date\n\n\ndef extract_dates(text):\n    out = []\n    for d, m, y in re.findall(r'(\\d{2})/(\\d{2})/(\\d{4})', text):\n        try:\n            out.append(date(int(y), int(m), int(d)).isoformat())\n        except ValueError:\n            pass\n    return out")],
                    max_tokens=1000, difficulty=3))

    group_cases = [
        {"script": "assert.strictEqual(JSON.stringify(groupBy([1, 2, 3, 4, 5], (x) => (x % 2 ? 'odd' : 'even'))), '{\"odd\":[1,3,5],\"even\":[2,4]}')"},
        {"script": "assert.strictEqual(JSON.stringify(groupBy(['a', 'bb', 'cc', 'd'], (s) => s.length)), '{\"1\":[\"a\",\"d\"],\"2\":[\"bb\",\"cc\"]}')"},
        {"script": "assert.strictEqual(JSON.stringify(groupBy([], (x) => x)), '{}')"},
    ]
    out.append(make(c, 13, "Raggruppare (JavaScript)", "Scrivi in JavaScript una funzione `groupBy(arr, fn)` che restituisce un oggetto le cui chiavi sono i valori di `fn(elemento)` e i cui valori sono gli array degli elementi "
                    f"corrispondenti, nell'ordine originale. {_JS_REPLY}", _spec("groupBy", group_cases, "javascript"), "groupBy(arr, fn) → oggetto chiave → array",
                    js("function groupBy(arr, fn) {\n  const out = {};\n  for (const x of arr) {\n    const key = fn(x);\n    if (!(key in out)) out[key] = [];\n    out[key].push(x);\n  }\n  return out;\n}"),
                    wrong=[js("function groupBy(arr, fn) {\n  const out = {};\n  for (const x of arr) out[fn(x)] = [x];\n  return out;\n}"), js("function groupBy(arr, fn) {\n  return arr.map((x) => [fn(x), x]);\n}")], max_tokens=800, difficulty=2))

    chunk_cases = [_call("chunk", [[1, 2, 3, 4, 5], 2], [[1, 2], [3, 4], [5]]), _call("chunk", [[], 3], []), _call("chunk", [[1, 2, 3], 5], [[1, 2, 3]]), _call("chunk", [[1, 2, 3, 4], 2], [[1, 2], [3, 4]]),
                   {"script": "assert.throws(() => chunk([1, 2], 0), RangeError)"}]
    out.append(make(c, 14, "Spezzare un array (JavaScript)", "Scrivi in JavaScript una funzione `chunk(arr, size)` che divide l'array in blocchi di lunghezza `size` (l'ultimo può essere più corto) e restituisce l'array dei blocchi. "
                    f"Se `size` non è positivo solleva un `RangeError`. {_JS_REPLY}", _spec("chunk", chunk_cases, "javascript"), "chunk(arr, size) → array di blocchi",
                    js("function chunk(arr, size) {\n  if (!(size > 0)) throw new RangeError('size must be positive');\n  const out = [];\n  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));\n  return out;\n}"),
                    wrong=[js("function chunk(arr, size) {\n  if (!(size > 0)) throw new RangeError('size must be positive');\n  const out = [];\n  for (let i = 0; i + size <= arr.length; i += size) out.push(arr.slice(i, i + size));\n  return out;\n}"),
                           js("function chunk(arr, size) {\n  const out = [];\n  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));\n  return out;\n}")], max_tokens=800, difficulty=2))
    return out
