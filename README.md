# Calendario Palancas FS

Resultados, clasificación, goleadores y calendario `.ics` de **PALANCAS FS** (Fútbol Sala, Móstoles), extraídos de [campeonatosmunicipalesmostoles.org](http://www.campeonatosmunicipalesmostoles.org/FutbolSala_27850.php).

## Web

Publicada en GitHub Pages. Suscríbete al calendario desde:

```
https://fernandogr98.github.io/palancasfs-calendario/palancas.ics
```

## Actualización automática

El calendario se actualiza solo **varias veces al día** (con especial vigilancia en las
franjas posteriores a los partidos) y se publica en GitHub Pages.

A diferencia de Achiperros, esta web **no tiene anti-bot**: los endpoints
`ser/*Service.php` devuelven JSON al pasar los parámetros de jqGrid. No hace falta
navegador headless.

El actualizador (en Hermes, fuera de este repo) ejecuta `scraper.py`, que regenera
`palancas.ics`, `matches.json`, `standings.json` y `scorers.json`.

## Estructura

- `scraper.py` — consulta los endpoints JSON y genera todos los ficheros de `docs/`.
- `docs/` — la web estática (HTML/JS + datos JSON + `.ics`).
- `.github/workflows/update.yml` — GitHub Action (respaldo, actualización manual).
