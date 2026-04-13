"use client";

export default function ReadingPane({
  content,
  onPlainClick,
}: {
  content: string;
  onPlainClick?: () => void;
}) {
  // Split content into paragraphs for nice rendering
  const paragraphs = content.split(/\n\s*\n/).filter(Boolean);

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
          className="mb-6 text-lg leading-8 text-gray-800 dark:text-gray-200 selection:bg-blue-200 dark:selection:bg-blue-800 selection:text-inherit"
        >
          {para}
        </p>
      ))}
    </article>
  );
}
