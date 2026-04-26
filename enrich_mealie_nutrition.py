#!/usr/bin/env python3
"""
Mealie-Rezepte via Spoonacular mit Naehrwerten anreichern.

Ablauf:
1) Rezepte aus Mealie laden
2) Zutaten in Spoonacular-lesbare Strings ueberfuehren
3) Spoonacular Analyze Recipe fuer Naehrwerte aufrufen
4) Naehrwerte zurueck nach Mealie patchen

Dry-Run vorhanden: Es werden keine Aenderungen in Mealie gespeichert.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class Config:
    mealie_base_url: str
    spoonacular_api_key: str
    mealie_token: str | None
    mealie_username: str | None
    mealie_password: str | None
    language: str
    per_page: int
    max_recipes: int | None
    dry_run: bool
    delay_seconds: float
    enable_show_nutrition: bool
    only_if_missing_nutrition: bool


class ApiError(RuntimeError):
    pass


class MealieClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if token:
            self.set_token(token)

    def set_token(self, token: str) -> None:
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def login(self, username: str, password: str) -> str:
        url = f"{self.base_url}/api/auth/token"
        data = {
            "username": username,
            "password": password,
            "remember_me": "true",
        }
        resp = self.session.post(url, data=data, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ApiError(
                f"Mealie Login fehlgeschlagen ({resp.status_code}): {resp.text[:500]}"
            )

        payload = resp.json()
        token = payload.get("access_token") or payload.get("token")
        if not token:
            raise ApiError("Mealie Login hat kein access_token geliefert.")

        self.set_token(token)
        return token

    def list_recipe_summaries(self, per_page: int = 100, max_recipes: int | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1

        while True:
            url = f"{self.base_url}/api/recipes"
            params = {"page": page, "perPage": per_page}
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code >= 400:
                raise ApiError(
                    f"Mealie Rezepte laden fehlgeschlagen ({resp.status_code}): {resp.text[:500]}"
                )

            data = resp.json()
            items = data.get("items", [])
            if not isinstance(items, list):
                raise ApiError("Unerwartetes Antwortformat bei /api/recipes (items fehlt).")

            results.extend(items)
            if max_recipes and len(results) >= max_recipes:
                return results[:max_recipes]

            if not data.get("next"):
                break

            page += 1

        return results

    def get_recipe(self, slug: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/recipes/{slug}"
        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ApiError(
                f"Mealie Rezept abrufen fehlgeschlagen fuer '{slug}' ({resp.status_code}): {resp.text[:500]}"
            )
        return resp.json()

    def patch_recipe(self, slug: str, patch_data: dict[str, Any]) -> None:
        url = f"{self.base_url}/api/recipes/{slug}"
        resp = self.session.patch(url, json=patch_data, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ApiError(
                f"Mealie Rezept patch fehlgeschlagen fuer '{slug}' ({resp.status_code}): {resp.text[:500]}"
            )


class SpoonacularClient:
    def __init__(self, api_key: str, timeout: int = 45) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    def analyze_recipe(
        self,
        title: str,
        servings: float | int | None,
        ingredients: list[str],
        instructions: str,
        language: str = "de",
    ) -> dict[str, Any]:
        url = "https://api.spoonacular.com/recipes/analyze"
        params = {
            "apiKey": self.api_key,
            "includeNutrition": "true",
            "language": language,
        }
        payload = {
            "title": title or "Untitled",
            "servings": servings if servings and servings > 0 else 1,
            "ingredients": ingredients,
            "instructions": instructions or "",
        }

        resp = self.session.post(url, params=params, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise ApiError(
                f"Spoonacular Analyze fehlgeschlagen ({resp.status_code}): {resp.text[:500]}"
            )

        return resp.json()


def build_ingredient_line(ingredient: dict[str, Any]) -> str:
    original_text = (ingredient.get("originalText") or "").strip()
    if original_text:
        return original_text

    display = (ingredient.get("display") or "").strip()
    if display:
        return display

    quantity = ingredient.get("quantity")
    qty_txt = ""
    if quantity is not None:
        try:
            q = float(quantity)
            qty_txt = str(int(q)) if q.is_integer() else str(q)
        except (ValueError, TypeError):
            qty_txt = str(quantity).strip()

    unit_name = ""
    unit = ingredient.get("unit")
    if isinstance(unit, dict):
        unit_name = (unit.get("name") or unit.get("abbreviation") or "").strip()

    food_name = ""
    food = ingredient.get("food")
    if isinstance(food, dict):
        food_name = (food.get("name") or "").strip()

    note = (ingredient.get("note") or "").strip()

    parts = [p for p in [qty_txt, unit_name, food_name] if p]
    line = " ".join(parts).strip()
    if note:
        line = f"{line}, {note}" if line else note

    return line or "unbekannte Zutat"


def extract_ingredient_lines(recipe: dict[str, Any]) -> list[str]:
    ingredients = recipe.get("recipeIngredient") or []
    if not isinstance(ingredients, list):
        return []
    return [build_ingredient_line(ing) for ing in ingredients]


def extract_instructions(recipe: dict[str, Any]) -> str:
    instructions = recipe.get("recipeInstructions") or []
    if not isinstance(instructions, list):
        return ""

    texts: list[str] = []
    for step in instructions:
        if not isinstance(step, dict):
            continue
        text = (step.get("text") or "").strip()
        if text:
            texts.append(text)

    return "\n".join(texts)


def nutrition_from_spoonacular(analysis: dict[str, Any]) -> dict[str, str | None]:
    nutrients = (((analysis or {}).get("nutrition") or {}).get("nutrients") or [])
    by_name: dict[str, dict[str, Any]] = {}

    if isinstance(nutrients, list):
        for nutrient in nutrients:
            if not isinstance(nutrient, dict):
                continue
            name = str(nutrient.get("name") or "").strip().lower()
            if name:
                by_name[name] = nutrient

    def fmt(name: str) -> str | None:
        n = by_name.get(name.lower())
        if not n:
            return None
        amount = n.get("amount")
        unit = (n.get("unit") or "").strip()
        if amount is None:
            return None
        try:
            val = float(amount)
            amount_txt = str(int(val)) if val.is_integer() else f"{val:.2f}".rstrip("0").rstrip(".")
        except (ValueError, TypeError):
            amount_txt = str(amount)
        return f"{amount_txt} {unit}".strip()

    return {
        "calories": fmt("Calories"),
        "carbohydrateContent": fmt("Carbohydrates"),
        "cholesterolContent": fmt("Cholesterol"),
        "fatContent": fmt("Fat"),
        "fiberContent": fmt("Fiber"),
        "proteinContent": fmt("Protein"),
        "saturatedFatContent": fmt("Saturated Fat"),
        "sodiumContent": fmt("Sodium"),
        "sugarContent": fmt("Sugar"),
        "transFatContent": fmt("Trans Fat"),
        "unsaturatedFatContent": fmt("Unsaturated Fat"),
    }


def load_config_file(path: str) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ApiError(f"Konfigurationsdatei nicht gefunden: {cfg_path}")

    try:
        raw = cfg_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Ungueltiges JSON in Konfigurationsdatei: {exc}") from exc

    if not isinstance(data, dict):
        raise ApiError("Konfigurationsdatei muss ein JSON-Objekt sein.")

    return data


def has_existing_nutrition(recipe: dict[str, Any]) -> bool:
    nutrition = recipe.get("nutrition")
    if not isinstance(nutrition, dict):
        return False

    return any(value not in (None, "") for value in nutrition.values())


def parse_args() -> Config:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default="config.json", help="Pfad zur JSON-Konfigurationsdatei")
    pre_args, _ = pre_parser.parse_known_args()

    config_data: dict[str, Any] = {}
    config_arg_explicit = "--config" in sys.argv

    if Path(pre_args.config).exists() or config_arg_explicit:
        try:
            config_data = load_config_file(pre_args.config)
        except ApiError as exc:
            raise SystemExit(f"[CONFIG-ERROR] {exc}")

    parser = argparse.ArgumentParser(
        description="Mealie Rezepte per Spoonacular mit Naehrwerten anreichern",
        parents=[pre_parser],
    )
    parser.add_argument(
        "--mealie-base-url",
        default=config_data.get("mealie_base_url"),
        help="z.B. https://mealie.example.com",
    )
    parser.add_argument(
        "--spoonacular-api-key",
        default=config_data.get("spoonacular_api_key"),
        help="Spoonacular API Key",
    )

    parser.add_argument(
        "--mealie-token",
        default=config_data.get("mealie_token"),
        help="Mealie API Token (Bearer)",
    )
    parser.add_argument(
        "--mealie-user",
        default=config_data.get("mealie_username"),
        help="Mealie Username/Email fuer Login",
    )

    parser.add_argument(
        "--mealie-password",
        default=config_data.get("mealie_password"),
        help="Passwort (nur mit --mealie-user)",
    )
    parser.add_argument(
        "--language",
        default=config_data.get("language", "de"),
        choices=["de", "en"],
        help="Spoonacular Sprache",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=int(config_data.get("per_page", 50)),
        help="Mealie Seitenlaenge",
    )
    parser.add_argument(
        "--max-recipes",
        type=int,
        default=config_data.get("max_recipes"),
        help="Optionales Limit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=bool(config_data.get("dry_run", False)),
        help="Keine Writes zu Mealie",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=float(config_data.get("delay_seconds", 0.25)),
        help="Pause zwischen Rezepten (Rate-Limit freundlich)",
    )
    parser.add_argument(
        "--enable-show-nutrition",
        action="store_true",
        default=bool(config_data.get("enable_show_nutrition", False)),
        help="Setzt zusaetzlich settings.showNutrition=true",
    )
    parser.add_argument(
        "--only-if-missing-nutrition",
        action="store_true",
        default=bool(config_data.get("only_if_missing_nutrition", False)),
        help="Ueberspringt Rezepte mit vorhandenen Naehrwerten (spart Spoonacular Requests)",
    )

    args = parser.parse_args()

    if not args.mealie_base_url:
        parser.error("mealie_base_url fehlt (CLI oder Konfigurationsdatei).")

    if not args.spoonacular_api_key:
        parser.error("spoonacular_api_key fehlt (CLI oder Konfigurationsdatei).")

    if args.mealie_token and args.mealie_user:
        parser.error("Nutze entweder mealie_token oder mealie_user/mealie_password, nicht beides.")

    if not args.mealie_token and not args.mealie_user:
        parser.error("Auth fehlt: mealie_token oder mealie_user/mealie_password angeben.")

    if args.mealie_user and not args.mealie_password:
        parser.error("Bei --mealie-user ist --mealie-password erforderlich.")

    return Config(
        mealie_base_url=args.mealie_base_url,
        spoonacular_api_key=args.spoonacular_api_key,
        mealie_token=args.mealie_token,
        mealie_username=args.mealie_user,
        mealie_password=args.mealie_password,
        language=args.language,
        per_page=args.per_page,
        max_recipes=args.max_recipes,
        dry_run=args.dry_run,
        delay_seconds=args.delay_seconds,
        enable_show_nutrition=args.enable_show_nutrition,
        only_if_missing_nutrition=args.only_if_missing_nutrition,
    )


def main() -> int:
    cfg = parse_args()

    mealie = MealieClient(cfg.mealie_base_url, token=cfg.mealie_token)
    spoon = SpoonacularClient(cfg.spoonacular_api_key)

    if not cfg.mealie_token and cfg.mealie_username and cfg.mealie_password:
        token = mealie.login(cfg.mealie_username, cfg.mealie_password)
        print("[INFO] Mealie Login erfolgreich (Token erhalten).")
        print(f"[INFO] Token Prefix: {token[:12]}...")

    summaries = mealie.list_recipe_summaries(per_page=cfg.per_page, max_recipes=cfg.max_recipes)
    print(f"[INFO] Gefundene Rezepte: {len(summaries)}")

    updated = 0
    skipped = 0
    failed = 0

    for idx, summary in enumerate(summaries, start=1):
        slug = str(summary.get("slug") or "").strip()
        name = str(summary.get("name") or "(ohne Name)").strip()

        if not slug:
            print(f"[WARN] Rezept ohne slug uebersprungen: {name}")
            skipped += 1
            continue

        print(f"\n[{idx}/{len(summaries)}] Rezept: {name} ({slug})")

        try:
            recipe = mealie.get_recipe(slug)

            if cfg.only_if_missing_nutrition and has_existing_nutrition(recipe):
                print("  [SKIP] Bereits vorhandene Naehrwerte -> kein Spoonacular Request")
                skipped += 1
                continue

            ingredient_lines = extract_ingredient_lines(recipe)
            if not ingredient_lines:
                print("  [WARN] Keine Zutaten gefunden -> skip")
                skipped += 1
                continue

            instructions = extract_instructions(recipe)
            servings = recipe.get("recipeServings") or 1

            analysis = spoon.analyze_recipe(
                title=name,
                servings=servings,
                ingredients=ingredient_lines,
                instructions=instructions,
                language=cfg.language,
            )
            nutrition = nutrition_from_spoonacular(analysis)

            patch_data: dict[str, Any] = {"nutrition": nutrition}
            if cfg.enable_show_nutrition:
                settings = recipe.get("settings") if isinstance(recipe.get("settings"), dict) else {}
                settings = dict(settings)
                settings["showNutrition"] = True
                patch_data["settings"] = settings

            if cfg.dry_run:
                print("  [DRY-RUN] Wuerde Mealie patchen mit nutrition:")
                print("  " + json.dumps(nutrition, ensure_ascii=True))
            else:
                mealie.patch_recipe(slug, patch_data)
                print("  [OK] Naehrwerte gespeichert")

            updated += 1

        except Exception as exc:  # noqa: BLE001
            print(f"  [ERROR] {exc}")
            failed += 1

        if cfg.delay_seconds > 0:
            time.sleep(cfg.delay_seconds)

    print("\n===== Zusammenfassung =====")
    print(f"Aktualisiert: {updated}")
    print(f"Uebersprungen: {skipped}")
    print(f"Fehler: {failed}")
    print(f"Dry-Run: {cfg.dry_run}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
