# Odysseus
## Bilder

Die Illustrationen stammen von Wikimedia Commons (gemeinfrei bzw. frei lizenziert).
Welche Werke verwendet werden, steht in `tools/images.json`. Der GitHub-Workflow
„Bilder von Wikimedia Commons laden“ (`.github/workflows/bilder.yml`) lädt sie,
prüft Lizenz und Motiv, verkleinert sie auf schlanke WebP-Dateien in `img/` und
trägt Bildmaße und Bildnachweis in `index.html` ein. Ergebnis: `tools/image-report.md`.
