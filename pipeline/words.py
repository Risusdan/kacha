"""Where a caption line may break: the word ends of a Traditional Chinese text, found with jieba.

jieba reads the dictionary `pipeline setup` downloads (see assets.dictionary_path) and the project's own
word list, caption_words.txt, for words the dictionary does not hold whole or gives too little weight.
"""

import functools
import logging
import warnings
from pathlib import Path

with warnings.catch_warnings():
    # jieba 0.42.1 has invalid escape sequences in two regular expressions; Python 3.12 prints a
    # SyntaxWarning for each when it compiles the package.
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

from pipeline.assets import dictionary_path, models_dir
from pipeline.errors import PipelineError

jieba.setLogLevel(logging.WARNING)  # no "Building prefix dict" lines on stderr

WORD_FREQ = 2000  # the weight of a listed word: high enough to beat the dictionary's own split of it
WORDS_FILE = Path(__file__).with_name("caption_words.txt")  # one word per line, empty lines skipped
CACHE_FILE = "jieba-dict.cache"  # jieba's cache of the parsed dictionary, written into models_dir()
NO_DICTIONARY = "caption dictionary not found: run `uv run pipeline setup`"


def _stamp(path: Path) -> tuple[Path, int]:
    return path, path.stat().st_mtime_ns


@functools.lru_cache(maxsize=1)
def _tokenizer(dictionary: tuple[Path, int], word_list: tuple[Path, int]) -> jieba.Tokenizer:
    """The tokenizer for a dictionary and a word list, each given as (path, mtime_ns): a file that changed
    is a new key, so the tokenizer is built again."""
    tokenizer = jieba.Tokenizer(dictionary=str(dictionary[0]))
    tokenizer.tmp_dir = str(models_dir())  # jieba writes its cache into the system temporary folder otherwise
    tokenizer.cache_file = CACHE_FILE
    for line in word_list[0].read_text(encoding="utf-8").splitlines():
        if line.strip():
            tokenizer.add_word(line.strip(), freq=WORD_FREQ)
    return tokenizer


def caption_inputs() -> dict[str, Path]:
    """The files word_ends reads, under the input names a step records them by: "words" (WORDS_FILE) and
    "dictionary". A step whose output holds caption lines lists them, so that a changed word list or
    dictionary makes it run again. Raises PipelineError when the dictionary has not been downloaded."""
    dictionary = dictionary_path()
    if not dictionary.is_file():
        raise PipelineError(NO_DICTIONARY)
    return {"words": WORDS_FILE, "dictionary": dictionary}


def word_ends(text: str, guess_new_words: bool = True) -> frozenset[int]:
    """The character index after each word of `text`, leaving out 0 and len(text): the places a line may break.

    Latin words and numbers (iPhone, 3.5) are one word each. With `guess_new_words` (jieba's HMM), a run of
    characters that are words of one character each may be guessed to be a new word, as caption breaking
    wants for names the dictionary lacks; without it only the dictionary and the word list make words, and
    every character no listed word holds is a word of its own. Raises PipelineError when the caption
    dictionary has not been downloaded.
    """
    dictionary = dictionary_path()
    if not dictionary.is_file():
        raise PipelineError(NO_DICTIONARY)
    tokenizer = _tokenizer(_stamp(dictionary), _stamp(WORDS_FILE))
    return frozenset(end for _, _, end in tokenizer.tokenize(text, HMM=guess_new_words) if 0 < end < len(text))
