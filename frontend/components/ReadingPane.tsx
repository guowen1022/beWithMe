"use client";

export default function ReadingPane({
  content,
  onPlainClick,
}: {
  content: string;
  onPlainClick?: () => void;
}) {
  // Split content into paragraphs with character offsets for scroll-to-section
  const paragraphs = (() => {
    const parts: { text: string; offset: number }[] = [];
    let offset = 0;
    for (const chunk of content.split(/\n\s*\n/)) {
      if (chunk.trim()) {
        const idx = content.indexOf(chunk, offset);
        parts.push({ text: chunk, offset: idx >= 0 ? idx : offset });
      }
      offset += chunk.length + 2; // account for \n\n
    }
    return parts;
  })();

  function handleClick() {
    if (!onPlainClick) return;
    const sel = window.getSelection();
    if (sel && sel.toString().trim().length > 0) return;
    onPlainClick();
  }

  return (
    <article
      data-selection-source="passage"
      onClick={handleClick}
      className="max-w-3xl mx-auto px-6 py-12 sm:px-12 sm:py-16"
    >
      {paragraphs.map((para, i) => (
        <p
          key={i}
          data-offset={para.offset}
          className="mb-6 text-lg leading-8 text-gray-800 dark:text-gray-200 selection:bg-blue-200 dark:selection:bg-blue-800 selection:text-inherit transition-colors duration-500"
        >
          {para.text}
        </p>
      ))}
    </article>
  );
}
