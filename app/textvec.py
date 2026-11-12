"""TF-IDF and word counts in plain numpy: what research_brain used scikit-learn (and with it SciPy, ~150 MB
of the install) for.

    TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1, max_df=0.9,
                    token_pattern=..., lowercase=True)
        .fit_transform(texts) -> Rows    .transform(texts) -> Rows    .get_feature_names_out() -> list[str]
    CountVectorizer(token_pattern=..., stop_words="english", lowercase=True)   (raw counts, no idf, no norm)
    ENGLISH_STOP_WORDS

The maths is scikit-learn's, so scores (and the thresholds tuned on them) stay the same: tokens from the
token pattern, stop words dropped before n-grams are formed, document frequency limits (an int is a count, a
float a share of the documents), smoothed idf = ln((1 + n) / (1 + df)) + 1, sublinear tf = 1 + ln(tf), rows
L2-normalised; terms in index order = sorted. ValueError when nothing is left to count, as scikit-learn.

Rows is a small compressed-row sparse matrix (data, indices, indptr) with what the callers need:
toarray(), slicing rows (m[1:], m[0:1]), dot_row(q) = m @ q.T for a one-row q, gram() = m @ m.T,
row_sums(), nnz and shape.
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np

DEFAULT_TOKENS = r"(?u)\b\w\w+\b"

# scikit-learn's English stop words (BSD licence; originally from the Glasgow Information Retrieval Group)
ENGLISH_STOP_WORDS = frozenset((
    "a", "about", "above", "across", "after", "afterwards", "again", "against", "all", "almost", "alone",
    "along", "already", "also", "although", "always", "am", "among", "amongst", "amoungst", "amount", "an",
    "and", "another", "any", "anyhow", "anyone", "anything", "anyway", "anywhere", "are", "around", "as",
    "at", "back", "be", "became", "because", "become", "becomes", "becoming", "been", "before", "beforehand",
    "behind", "being", "below", "beside", "besides", "between", "beyond", "bill", "both", "bottom", "but",
    "by", "call", "can", "cannot", "cant", "co", "con", "could", "couldnt", "cry", "de", "describe", "detail",
    "do", "done", "down", "due", "during", "each", "eg", "eight", "either", "eleven", "else", "elsewhere",
    "empty", "enough", "etc", "even", "ever", "every", "everyone", "everything", "everywhere", "except",
    "few", "fifteen", "fifty", "fill", "find", "fire", "first", "five", "for", "former", "formerly", "forty",
    "found", "four", "from", "front", "full", "further", "get", "give", "go", "had", "has", "hasnt", "have",
    "he", "hence", "her", "here", "hereafter", "hereby", "herein", "hereupon", "hers", "herself", "him",
    "himself", "his", "how", "however", "hundred", "i", "ie", "if", "in", "inc", "indeed", "interest", "into",
    "is", "it", "its", "itself", "keep", "last", "latter", "latterly", "least", "less", "ltd", "made", "many",
    "may", "me", "meanwhile", "might", "mill", "mine", "more", "moreover", "most", "mostly", "move", "much",
    "must", "my", "myself", "name", "namely", "neither", "never", "nevertheless", "next", "nine", "no",
    "nobody", "none", "noone", "nor", "not", "nothing", "now", "nowhere", "of", "off", "often", "on", "once",
    "one", "only", "onto", "or", "other", "others", "otherwise", "our", "ours", "ourselves", "out", "over",
    "own", "part", "per", "perhaps", "please", "put", "rather", "re", "same", "see", "seem", "seemed",
    "seeming", "seems", "serious", "several", "she", "should", "show", "side", "since", "sincere", "six",
    "sixty", "so", "some", "somehow", "someone", "something", "sometime", "sometimes", "somewhere", "still",
    "such", "system", "take", "ten", "than", "that", "the", "their", "them", "themselves", "then", "thence",
    "there", "thereafter", "thereby", "therefore", "therein", "thereupon", "these", "they", "thick", "thin",
    "third", "this", "those", "though", "three", "through", "throughout", "thru", "thus", "to", "together",
    "too", "top", "toward", "towards", "twelve", "twenty", "two", "un", "under", "until", "up", "upon", "us",
    "very", "via", "was", "we", "well", "were", "what", "whatever", "when", "whence", "whenever", "where",
    "whereafter", "whereas", "whereby", "wherein", "whereupon", "wherever", "whether", "which", "while",
    "whither", "who", "whoever", "whole", "whom", "whose", "why", "will", "with", "within", "without",
    "would", "yet", "you", "your", "yours", "yourself", "yourselves",
))


class Rows:
    """A compressed sparse row matrix: row i is data[indptr[i]:indptr[i+1]] at columns indices[...]."""

    def __init__(self, data: np.ndarray, indices: np.ndarray, indptr: np.ndarray, n_cols: int):
        self.data, self.indices, self.indptr = data, indices, indptr
        self.shape = (len(indptr) - 1, n_cols)

    @property
    def nnz(self) -> int:
        return int(self.indptr[-1] - self.indptr[0])

    def __getitem__(self, key: slice) -> "Rows":
        if not isinstance(key, slice):
            raise TypeError("Rows only slices rows: m[a:b]")
        a, b, _ = key.indices(self.shape[0])
        b = max(a, b)
        lo, hi = self.indptr[a], self.indptr[b]
        return Rows(self.data[lo:hi], self.indices[lo:hi], self.indptr[a:b + 1] - lo, self.shape[1])

    def _row_ids(self) -> np.ndarray:
        return np.repeat(np.arange(self.shape[0]), np.diff(self.indptr))

    def toarray(self) -> np.ndarray:
        out = np.zeros(self.shape, dtype=self.data.dtype if self.data.size else np.float64)
        out[self._row_ids(), self.indices] = self.data
        return out

    def row_sums(self) -> np.ndarray:
        return np.bincount(self._row_ids(), weights=self.data, minlength=self.shape[0])

    def dot_row(self, q: "Rows") -> np.ndarray:
        """self @ q.T for a one-row q: one score per row of self."""
        dense = np.zeros(self.shape[1], dtype=np.float64)
        lo, hi = q.indptr[0], q.indptr[1]
        dense[q.indices[lo:hi]] = q.data[lo:hi]
        return np.bincount(self._row_ids(), weights=self.data * dense[self.indices], minlength=self.shape[0])

    def gram(self) -> np.ndarray:
        """self @ self.T, dense (n x n)."""
        n, v = self.shape
        if n * v <= 20_000_000:
            d = self.toarray()
            return d @ d.T
        return np.vstack([self.dot_row(self[i:i + 1]) for i in range(n)])


class CountVectorizer:
    """Term counts per document (scikit-learn's CountVectorizer, for the options research_brain uses)."""

    def __init__(self, *, lowercase: bool = True, token_pattern: str = DEFAULT_TOKENS, stop_words=None,
                 ngram_range: tuple[int, int] = (1, 1), min_df: float | int = 1, max_df: float | int = 1.0):
        self.lowercase, self.ngram_range = lowercase, ngram_range
        self.min_df, self.max_df = min_df, max_df
        self._token = re.compile(token_pattern)
        self._stop = ENGLISH_STOP_WORDS if stop_words == "english" else frozenset(stop_words or ())
        self.vocabulary_: dict[str, int] = {}

    def _analyze(self, doc: str) -> list[str]:
        tokens = self._token.findall(doc.lower() if self.lowercase else doc)
        if self._stop:
            tokens = [t for t in tokens if t not in self._stop]
        lo, hi = self.ngram_range
        if hi == 1:
            return tokens
        out = list(tokens) if lo == 1 else []
        for n in range(max(lo, 2), min(hi, len(tokens)) + 1):
            out += [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
        return out

    def _counts(self, texts) -> list[Counter]:
        return [Counter(self._analyze(t or "")) for t in texts]

    def _fit_vocabulary(self, counts: list[Counter]) -> np.ndarray:
        """Learn the vocabulary (sorted, document-frequency limits applied); returns each term's df."""
        df: Counter = Counter()
        for c in counts:
            df.update(c.keys())
        if not df:
            raise ValueError("empty vocabulary; perhaps the documents only contain stop words")
        n = len(counts)
        hi = self.max_df if isinstance(self.max_df, int) and not isinstance(self.max_df, bool) else self.max_df * n
        lo = self.min_df if isinstance(self.min_df, int) and not isinstance(self.min_df, bool) else self.min_df * n
        if hi < lo:
            raise ValueError("max_df corresponds to < documents than min_df")
        terms = sorted(t for t, k in df.items() if lo <= k <= hi)
        if not terms:
            raise ValueError("After pruning, no terms remain. Try a lower min_df or a higher max_df.")
        self.vocabulary_ = {t: i for i, t in enumerate(terms)}
        return np.array([df[t] for t in terms], dtype=np.float64)

    def _matrix(self, counts: list[Counter]) -> Rows:
        vocab = self.vocabulary_
        indptr, indices, data = [0], [], []
        for c in counts:
            row = sorted((vocab[t], k) for t, k in c.items() if t in vocab)
            indices += [i for i, _ in row]
            data += [k for _, k in row]
            indptr.append(len(indices))
        return Rows(np.asarray(data, dtype=np.float64), np.asarray(indices, dtype=np.int64),
                    np.asarray(indptr, dtype=np.int64), len(vocab))

    def fit_transform(self, texts) -> Rows:
        counts = self._counts(texts)
        self._fit_vocabulary(counts)
        return self._matrix(counts)

    def transform(self, texts) -> Rows:
        return self._matrix(self._counts(texts))

    def get_feature_names_out(self) -> list[str]:
        return sorted(self.vocabulary_, key=self.vocabulary_.get)


class TfidfVectorizer(CountVectorizer):
    """TF-IDF with smoothed idf and L2-normalised rows (scikit-learn's defaults), optionally sublinear tf."""

    def __init__(self, *, sublinear_tf: bool = False, **kw):
        super().__init__(**kw)
        self.sublinear_tf = sublinear_tf
        self.idf_: np.ndarray | None = None

    def _weigh(self, m: Rows) -> Rows:
        data = m.data.copy()
        if self.sublinear_tf and data.size:
            data = np.log(data) + 1.0
        data *= self.idf_[m.indices]
        rows = m._row_ids()
        norms = np.sqrt(np.bincount(rows, weights=data * data, minlength=m.shape[0]))
        norms[norms == 0] = 1.0
        data /= norms[rows]
        return Rows(data, m.indices, m.indptr, m.shape[1])

    def fit_transform(self, texts) -> Rows:
        counts = self._counts(texts)
        df = self._fit_vocabulary(counts)
        self.idf_ = np.log((1.0 + len(counts)) / (1.0 + df)) + 1.0
        return self._weigh(self._matrix(counts))

    def transform(self, texts) -> Rows:
        return self._weigh(self._matrix(self._counts(texts)))
