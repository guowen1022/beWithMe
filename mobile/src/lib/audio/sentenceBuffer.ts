// Sentence-boundary chunker matching services/persona/routers/ask.py:
//   _SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=\S)")
//
// We don't have lookbehind support guaranteed in older JS engines; reproduce
// the same semantics by manually scanning. Also ports _strip_for_speech for
// stripping markdown noise the LLM sometimes leaks.

const SENTENCE_END = /[.!?]/;

export function stripForSpeech(text: string): string {
  let out = text.replace(/\*\*/g, "").replace(/__/g, "");
  out = out.replace(/^[\s>*\-]+/, "");
  return out.trim();
}

export class SentenceBuffer {
  private buf = "";
  constructor(private readonly onSentence: (sentence: string) => void) {}

  append(text: string): void {
    this.buf += text;
    let lastFire = 0;
    for (let i = 0; i < this.buf.length - 1; i++) {
      if (!SENTENCE_END.test(this.buf[i])) continue;
      // Require whitespace + non-whitespace after the terminator.
      const next = this.buf[i + 1];
      if (next === " " || next === "\n" || next === "\t") {
        let j = i + 1;
        while (j < this.buf.length && /\s/.test(this.buf[j])) j++;
        if (j < this.buf.length) {
          const sentence = stripForSpeech(this.buf.slice(lastFire, i + 1));
          if (sentence) this.onSentence(sentence);
          lastFire = j;
          i = j - 1;
        }
      }
    }
    this.buf = this.buf.slice(lastFire);
  }

  // Call at stream end to flush any trailing partial sentence.
  flush(): void {
    const tail = stripForSpeech(this.buf);
    this.buf = "";
    if (tail) this.onSentence(tail);
  }
}
