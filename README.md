# Mealie Naehrwert-Anreicherung via Spoonacular

Dieses Script holt alle Rezepte aus Mealie, bereitet Zutaten fuer Spoonacular auf, holt Naehrwerte und schreibt sie zurueck nach Mealie.

## Voraussetzungen

- Python 3.10+
- Mealie API erreichbar
- Spoonacular API Key
- Mealie Token **oder** User/Passwort

## Installation

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

## Konfiguration per Datei

Das Script liest standardmaessig eine `config.json` im Projektordner.

Als Vorlage ist `config.example.json` enthalten.

Start mit Konfigurationsdatei:

```bash
python enrich_mealie_nutrition.py --config config.example.json
```

CLI-Parameter ueberschreiben Werte aus der Datei:

```bash
python enrich_mealie_nutrition.py --config config.example.json --max-recipes 10 --dry-run
```

Empfehlung zum Sparen von Spoonacular-Requests:

- Setze `only_if_missing_nutrition` auf `true`, dann werden Rezepte mit vorhandenen Naehrwerten komplett uebersprungen.

## Dry-Run (empfohlen zuerst)

```bash
python enrich_mealie_nutrition.py ^
  --config "config.example.json" ^
  --mealie-base-url "https://mealie.deine-domain.tld" ^
  --spoonacular-api-key "DEIN_KEY" ^
  --mealie-token "DEIN_MEALIE_TOKEN" ^
  --dry-run
```

## Mit Mealie User/Passwort

```bash
python enrich_mealie_nutrition.py ^
  --config "config.example.json" ^
  --mealie-base-url "https://mealie.deine-domain.tld" ^
  --spoonacular-api-key "DEIN_KEY" ^
  --mealie-user "dein.user" ^
  --mealie-password "deinPasswort" ^
  --dry-run
```

## Echte Speicherung (ohne Dry-Run)

```bash
python enrich_mealie_nutrition.py ^
  --config "config.example.json" ^
  --mealie-base-url "https://mealie.deine-domain.tld" ^
  --spoonacular-api-key "DEIN_KEY" ^
  --mealie-token "DEIN_MEALIE_TOKEN"
```

Optional:

- `--max-recipes 10` testet nur die ersten 10 Rezepte
- `--delay-seconds 0.5` entlastet API-Limits
- `--enable-show-nutrition` setzt `settings.showNutrition=true`
- `--only-if-missing-nutrition` ueberspringt Rezepte mit vorhandenen Naehrwerten
- `--language de` oder `--language en`

## Was genau gemappt wird

Spoonacular-Nutrients -> Mealie `nutrition`:

- Calories -> `calories`
- Carbohydrates -> `carbohydrateContent`
- Cholesterol -> `cholesterolContent`
- Fat -> `fatContent`
- Fiber -> `fiberContent`
- Protein -> `proteinContent`
- Saturated Fat -> `saturatedFatContent`
- Sodium -> `sodiumContent`
- Sugar -> `sugarContent`
- Trans Fat -> `transFatContent`
- Unsaturated Fat -> `unsaturatedFatContent`
