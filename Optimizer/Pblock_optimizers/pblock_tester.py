#!/usr/bin/env python3

import argparse
import math
import random
from dataclasses import dataclass
from typing import List, Tuple

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


@dataclass
class Candidate:
    x: int
    y: int
    w: int
    h: int
    score: float = float("-inf")
    generation: int = 0
    parent: int = -1
    origin: str = "unknown"


class SearchEnvironment:
    def __init__(self, board_w: int, board_h: int, seed: int = 0):
        self.board_w = board_w
        self.board_h = board_h
        self.rng = random.Random(seed)

        # Hidden synthetic "timing potential" landscape.
        self.good_centers = [
            (int(board_w * 0.20), int(board_h * 0.75), 1.0),
            (int(board_w * 0.72), int(board_h * 0.28), 1.4),
            (int(board_w * 0.82), int(board_h * 0.80), 1.2),
        ]
        self.bad_centers = [
            (int(board_w * 0.50), int(board_h * 0.50), 1.0),
            (int(board_w * 0.15), int(board_h * 0.20), 0.7),
        ]

        self.target_area = int(board_w * board_h * 0.035)

    def clamp_candidate(self, c: Candidate) -> Candidate:
        w = max(4, min(c.w, self.board_w))
        h = max(4, min(c.h, self.board_h))
        x = max(0, min(c.x, self.board_w - w))
        y = max(0, min(c.y, self.board_h - h))
        return Candidate(
            x=x,
            y=y,
            w=w,
            h=h,
            score=c.score,
            generation=c.generation,
            parent=c.parent,
            origin=c.origin,
        )

    def random_candidate(self, generation: int = 0, origin: str = "random") -> Candidate:
        w = self.rng.randint(max(4, self.board_w // 12), max(6, self.board_w // 5))
        h = self.rng.randint(max(4, self.board_h // 12), max(6, self.board_h // 5))
        x = self.rng.randint(0, max(0, self.board_w - w))
        y = self.rng.randint(0, max(0, self.board_h - h))
        return Candidate(x=x, y=y, w=w, h=h, generation=generation, origin=origin)

    def candidate_from_region(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        generation: int = 0,
        origin: str = "seed",
    ) -> Candidate:
        return self.clamp_candidate(
            Candidate(
                x=x,
                y=y,
                w=w,
                h=h,
                generation=generation,
                origin=origin,
            )
        )

    def mutate(self, c: Candidate, generation: int) -> Candidate:
        dx = self.rng.randint(-12, 12)
        dy = self.rng.randint(-12, 12)
        dw = self.rng.randint(-8, 8)
        dh = self.rng.randint(-8, 8)

        # Sometimes make a large jump.
        if self.rng.random() < 0.25:
            dx += self.rng.randint(-40, 40)
            dy += self.rng.randint(-40, 40)

        child = Candidate(
            x=c.x + dx,
            y=c.y + dy,
            w=c.w + dw,
            h=c.h + dh,
            generation=generation,
            parent=id(c),
            origin="mutation",
        )
        return self.clamp_candidate(child)

    def score(self, c: Candidate) -> float:
        cx = c.x + c.w / 2.0
        cy = c.y + c.h / 2.0

        value = 0.0
        for gx, gy, amp in self.good_centers:
            d2 = (cx - gx) ** 2 + (cy - gy) ** 2
            value += amp * math.exp(-d2 / 900.0)

        for bx, by, amp in self.bad_centers:
            d2 = (cx - bx) ** 2 + (cy - by) ** 2
            value -= amp * math.exp(-d2 / 700.0)

        area = c.w * c.h
        area_penalty = abs(area - self.target_area) / max(1.0, self.target_area)
        value -= 0.30 * area_penalty

        edge_margin = min(c.x, c.y, self.board_w - (c.x + c.w), self.board_h - (c.y + c.h))
        if edge_margin < 2:
            value -= 0.20

        value += self.rng.uniform(-0.03, 0.03)
        return value

    def background_grid(self) -> List[List[float]]:
        grid = []
        for y in range(self.board_h):
            row = []
            for x in range(self.board_w):
                v = 0.0
                for gx, gy, amp in self.good_centers:
                    d2 = (x - gx) ** 2 + (y - gy) ** 2
                    v += amp * math.exp(-d2 / 900.0)
                for bx, by, amp in self.bad_centers:
                    d2 = (x - bx) ** 2 + (y - by) ** 2
                    v -= amp * math.exp(-d2 / 700.0)
                row.append(v)
            grid.append(row)
        return grid


def initialize_population(
    env: SearchEnvironment,
    population_size: int,
    start_region: Candidate,
    include_start_in_population: bool,
    local_around_start: int,
) -> List[Candidate]:
    population: List[Candidate] = []

    if include_start_in_population:
        population.append(start_region)

    # A few local variants are allowed, but they do not dominate the population.
    for _ in range(local_around_start):
        population.append(env.mutate(start_region, generation=0))

    # The rest of the population is global random exploration.
    while len(population) < population_size:
        population.append(env.random_candidate(generation=0, origin="global_random"))

    return population


def evolutionary_search(
    env: SearchEnvironment,
    population_size: int,
    elite_count: int,
    generations: int,
    inject_random: int,
    start_region: Candidate,
    include_start_in_population: bool,
    local_around_start: int,
) -> Tuple[List[Candidate], List[Candidate], Candidate]:
    population = initialize_population(
        env=env,
        population_size=population_size,
        start_region=start_region,
        include_start_in_population=include_start_in_population,
        local_around_start=local_around_start,
    )

    history: List[Candidate] = []
    best_per_gen: List[Candidate] = []

    for gen in range(generations):
        scored = []
        for c in population:
            c = env.clamp_candidate(c)
            c.score = env.score(c)
            c.generation = gen
            scored.append(c)
            history.append(c)

        scored.sort(key=lambda c: c.score, reverse=True)
        best_per_gen.append(scored[0])

        elites = scored[:elite_count]
        next_pop = elites[:]

        while len(next_pop) < population_size - inject_random:
            parent = env.rng.choice(elites)
            child = env.mutate(parent, generation=gen + 1)
            next_pop.append(child)

        while len(next_pop) < population_size:
            next_pop.append(env.random_candidate(generation=gen + 1, origin="global_random"))

        population = next_pop

    final_best = max(best_per_gen, key=lambda c: c.score)
    return history, best_per_gen, final_best


def plot_search(
    env: SearchEnvironment,
    history: List[Candidate],
    best_per_gen: List[Candidate],
    final_best: Candidate,
    start_region: Candidate,
    show_every: int,
) -> None:
    bg = env.background_grid()

    plt.figure(figsize=(10, 8))
    plt.imshow(bg, origin="lower", extent=[0, env.board_w, 0, env.board_h], alpha=0.75)
    plt.colorbar(label="Synthetic timing potential")

    shown = history[::max(1, show_every)]
    xs = [c.x + c.w / 2.0 for c in shown]
    ys = [c.y + c.h / 2.0 for c in shown]
    cs = [c.generation for c in shown]
    plt.scatter(xs, ys, c=cs, s=18, alpha=0.45, label="Explored candidates")

    # Draw historical best boxes lightly.
    for best in best_per_gen[:-1]:
        rect = Rectangle((best.x, best.y), best.w, best.h, fill=False, linewidth=1.0, alpha=0.20)
        plt.gca().add_patch(rect)

    # Draw start region distinctly.
    start_rect = Rectangle(
        (start_region.x, start_region.y),
        start_region.w,
        start_region.h,
        fill=False,
        linewidth=2.5,
        linestyle="--",
    )
    plt.gca().add_patch(start_rect)
    plt.text(
        start_region.x,
        start_region.y + start_region.h + 2,
        "Start region",
        fontsize=10,
        ha="left",
        va="bottom",
    )

    # Draw final best.
    best_rect = Rectangle(
        (final_best.x, final_best.y),
        final_best.w,
        final_best.h,
        fill=False,
        linewidth=3.0,
    )
    plt.gca().add_patch(best_rect)
    plt.text(
        final_best.x,
        final_best.y - 2,
        "Final best",
        fontsize=10,
        ha="left",
        va="top",
    )

    plt.title("Proof-of-concept pblock search visualization")
    plt.xlabel("Board X")
    plt.ylabel("Board Y")
    plt.legend(loc="upper right")
    plt.tight_layout()
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="Visual proof-of-concept for pblock search logic.")
    parser.add_argument("--board-w", type=int, default=120)
    parser.add_argument("--board-h", type=int, default=90)
    parser.add_argument("--population", type=int, default=36)
    parser.add_argument("--elite", type=int, default=6)
    parser.add_argument("--generations", type=int, default=30)
    parser.add_argument("--inject-random", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--show-every", type=int, default=3)

    # Explicit starting region to reflect a pre-existing FPGA cluster.
    parser.add_argument("--start-x", type=int, default=25)
    parser.add_argument("--start-y", type=int, default=30)
    parser.add_argument("--start-w", type=int, default=18)
    parser.add_argument("--start-h", type=int, default=14)

    # Controls whether the start is merely labeled or also included as an actual candidate.
    parser.add_argument("--include-start-in-population", action="store_true")
    parser.add_argument("--local-around-start", type=int, default=2)

    args = parser.parse_args()

    env = SearchEnvironment(args.board_w, args.board_h, seed=args.seed)

    start_region = env.candidate_from_region(
        args.start_x,
        args.start_y,
        args.start_w,
        args.start_h,
        generation=0,
        origin="start_region",
    )

    history, best_per_gen, final_best = evolutionary_search(
        env=env,
        population_size=args.population,
        elite_count=args.elite,
        generations=args.generations,
        inject_random=args.inject_random,
        start_region=start_region,
        include_start_in_population=args.include_start_in_population,
        local_around_start=args.local_around_start,
    )

    print("Start region:")
    print("  x =", start_region.x)
    print("  y =", start_region.y)
    print("  w =", start_region.w)
    print("  h =", start_region.h)

    print("")
    print("Best synthetic candidate:")
    print("  x =", final_best.x)
    print("  y =", final_best.y)
    print("  w =", final_best.w)
    print("  h =", final_best.h)
    print("  score =", final_best.score)

    plot_search(
        env=env,
        history=history,
        best_per_gen=best_per_gen,
        final_best=final_best,
        start_region=start_region,
        show_every=args.show_every,
    )


if __name__ == "__main__":
    main()