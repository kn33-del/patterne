import type { RecipeName } from "../lib/api";
import { recipeLabel } from "../lib/format";

type RecipeCardConfig = {
  recipe: RecipeName;
  accent: string;
  summary: string;
  runtime: string;
  risk: string;
};

const recipeCards: RecipeCardConfig[] = [
  {
    recipe: "quick_timing_rescue",
    accent: "Fast",
    summary: "Short pblock sweep for near-closure designs.",
    runtime: "Minutes",
    risk: "Low"
  },
  {
    recipe: "pblock_explorer",
    accent: "Physical",
    summary: "Broader region search with attempt ranking.",
    runtime: "Medium",
    risk: "Medium"
  },
  {
    recipe: "high_fanout_optimization",
    accent: "Fanout",
    summary: "Prioritize high-fanout cleanup with the existing optimizer path.",
    runtime: "Medium",
    risk: "Medium"
  },
  {
    recipe: "ai_recommended_plan",
    accent: "Guide",
    summary: "Generate a run plan before execution.",
    runtime: "Instant",
    risk: "Low"
  },
  {
    recipe: "ai_autopilot",
    accent: "AI",
    summary: "LLM-guided search over the current optimization stack.",
    runtime: "Long",
    risk: "Experimental"
  }
];

export function RecipeCards({
  onSelect,
  activeRecipe,
  enabledRecipes
}: {
  onSelect?: (recipe: RecipeName) => void;
  activeRecipe?: RecipeName | null;
  enabledRecipes?: RecipeName[];
}) {
  const enabled = new Set(enabledRecipes ?? recipeCards.map((card) => card.recipe));

  return (
    <div className="recipe-grid">
      {recipeCards.map((card) => {
        const selectable = enabled.has(card.recipe);
        const active = activeRecipe === card.recipe;
        return (
          <article
            key={card.recipe}
            className={`panel recipe-card ${active ? "recipe-active" : ""} ${selectable ? "" : "recipe-disabled"}`}
          >
            <div className="recipe-header">
              <div>
                <span className="eyebrow">{card.accent}</span>
                <h3>{recipeLabel(card.recipe)}</h3>
              </div>
              <span className="badge">{card.runtime}</span>
            </div>
            <p>{card.summary}</p>
            <div className="recipe-meta">
              <span>Risk {card.risk}</span>
            </div>
            <button className="button" onClick={() => selectable && onSelect?.(card.recipe)} disabled={!selectable}>
              {card.recipe === "ai_recommended_plan" ? "Open Plan" : "Use"}
            </button>
          </article>
        );
      })}
    </div>
  );
}
