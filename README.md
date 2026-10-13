# mijnwebservice-tools

Gereedschap voor sites van mijnwebservice.

## kwaliteitscheck.py
Harde controles die elke site moet halen voor hij naar een klant gaat: Google-instellingen per modus (demo of oplevering), hero op vier schermformaten, overlap, scherpte van beelden, verboden woorden en tekens, pijltjes, labels, dode links, favicon, OG-afbeelding en foutmeldingen in de console.

In een Lovable-project:

```
mkdir -p scripts
curl -sL https://raw.githubusercontent.com/juliusdekort-ctrl/mijnwebservice-tools/main/kwaliteitscheck.py -o scripts/kwaliteitscheck.py
python3 scripts/kwaliteitscheck.py http://localhost:8080 --modus demo
```

Gebruik `--modus oplevering` voor een live site. Uitkomst per controle PASS, WARN of FAIL; exit-code 1 bij een FAIL.
