"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  getRecommendations,
  generateRecommendations,
  updateRecommendation,
  getCurrentUserId,
  RecommendationItem,
} from "@/lib/api";

const CATEGORY_LABELS: Record<string, { label: string; color: string }> = {
  review: { label: "Review", color: "bg-amber-100 text-amber-800" },
  explore: { label: "Explore", color: "bg-blue-100 text-blue-800" },
  deepen: { label: "Deepen", color: "bg-purple-100 text-purple-800" },
  article: { label: "Article", color: "bg-green-100 text-green-800" },
};

const SOURCE_LABELS: Record<string, string> = {
  llm: "AI",
  web: "Web",
};

function RecommendationCard({
  rec,
  onDismiss,
  onAccept,
}: {
  rec: RecommendationItem;
  onDismiss: (id: string) => void;
  onAccept: (id: string) => void;
}) {
  const cat = CATEGORY_LABELS[rec.category] || {
    label: rec.category,
    color: "bg-gray-100 text-gray-800",
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2 mb-2">
        <h3 className="font-semibold text-gray-900 text-sm leading-tight">
          {rec.title}
        </h3>
        <div className="flex gap-1 shrink-0">
          <span
            className={`text-xs px-2 py-0.5 rounded-full font-medium ${cat.color}`}
          >
            {cat.label}
          </span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
            {SOURCE_LABELS[rec.source] || rec.source}
          </span>
        </div>
      </div>

      <p className="text-sm text-gray-600 mb-2">{rec.summary}</p>

      <p className="text-xs text-gray-400 italic mb-3">{rec.reasoning}</p>

      {rec.concept_names.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {rec.concept_names.map((name) => (
            <span
              key={name}
              className="text-xs px-2 py-0.5 rounded bg-gray-50 text-gray-500 border border-gray-200"
            >
              {name}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          <button
            onClick={() => onAccept(rec.id)}
            className="text-xs px-3 py-1.5 rounded bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            Start Learning
          </button>
          <button
            onClick={() => onDismiss(rec.id)}
            className="text-xs px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            Dismiss
          </button>
        </div>
        {rec.url && (
          <a
            href={rec.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-500 hover:underline"
          >
            Source
          </a>
        )}
      </div>
    </div>
  );
}

export default function RecommendationsPage() {
  const router = useRouter();
  const [recs, setRecs] = useState<RecommendationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRecs = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getRecommendations();
      setRecs(data);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === "unknown_user") {
        router.push("/");
        return;
      }
      setError("Failed to load recommendations");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!getCurrentUserId()) {
      router.push("/");
      return;
    }
    loadRecs();
  }, [loadRecs, router]);

  const handleGenerate = async () => {
    try {
      setGenerating(true);
      setError(null);
      const data = await generateRecommendations();
      setRecs(data);
    } catch (e: unknown) {
      if (e instanceof Error && e.message === "unknown_user") {
        router.push("/");
        return;
      }
      setError("Failed to generate recommendations");
    } finally {
      setGenerating(false);
    }
  };

  const handleDismiss = async (id: string) => {
    try {
      await updateRecommendation(id, "dismissed");
      setRecs((prev) => prev.filter((r) => r.id !== id));
    } catch {
      // ignore
    }
  };

  const handleAccept = async (id: string) => {
    try {
      await updateRecommendation(id, "accepted");
      setRecs((prev) => prev.filter((r) => r.id !== id));
    } catch {
      // ignore
    }
  };

  // Group by category
  const grouped = recs.reduce(
    (acc, rec) => {
      const key = rec.category;
      if (!acc[key]) acc[key] = [];
      acc[key].push(rec);
      return acc;
    },
    {} as Record<string, RecommendationItem[]>
  );

  const categoryOrder = ["review", "explore", "deepen", "article"];
  const sortedCategories = Object.keys(grouped).sort(
    (a, b) => categoryOrder.indexOf(a) - categoryOrder.indexOf(b)
  );

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Recommendations</h1>
        <button
          onClick={handleGenerate}
          disabled={generating}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {generating ? "Generating..." : "Refresh Recommendations"}
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-12 text-gray-500">
          Loading recommendations...
        </div>
      ) : recs.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-gray-500 mb-4">No recommendations yet.</p>
          <p className="text-sm text-gray-400 mb-4">
            Click &quot;Refresh Recommendations&quot; to generate personalized
            suggestions based on your learning history.
          </p>
        </div>
      ) : (
        <div className="space-y-8">
          {sortedCategories.map((category) => {
            const cat = CATEGORY_LABELS[category] || {
              label: category,
              color: "",
            };
            return (
              <section key={category}>
                <h2 className="text-lg font-semibold text-gray-700 mb-3">
                  {cat.label}
                </h2>
                <div className="grid gap-3 sm:grid-cols-2">
                  {grouped[category].map((rec) => (
                    <RecommendationCard
                      key={rec.id}
                      rec={rec}
                      onDismiss={handleDismiss}
                      onAccept={handleAccept}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
