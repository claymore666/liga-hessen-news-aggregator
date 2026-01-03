# Kostenlose LLM-APIs für automatisierte Daily-Briefings 2025/2026

Ein automatisiertes Daily-Briefing-System mit **50-100 Artikeln täglich** kann mit einer Kombination aus OpenRouter, Groq und Mistral vollständig kostenlos betrieben werden. Google Gemini hat die kostenlosen Kontingente im Dezember 2025 drastisch gekürzt, und Ollama bietet seit September 2025 erstmals einen Cloud-Service an – allerdings mit nur **5 kostenlosen Premium-Requests pro Monat**.

## Ollama Cloud existiert – aber mit starken Einschränkungen

Entgegen der früheren Annahme, dass Ollama ausschließlich für lokale Nutzung gedacht ist, hat das Unternehmen im September 2025 mit **Ollama v0.12** einen offiziellen Cloud-Service gestartet. Der Free-Plan bietet lediglich 5 Premium-Requests pro Monat, was für ein Daily-Briefing-System ungeeignet ist. Die kostenpflichtigen Pläne liegen bei **$20/Monat** (Pro, 20 Requests) bzw. **$100/Monat** (Max, 100 Requests).

Ollama Cloud richtet sich primär an Nutzer, die lokale Workflows mit gelegentlichem Zugriff auf große Cloud-Modelle wie Gemini 3 Pro Preview oder DeepSeek-V3.1 erweitern möchten. Für kontinuierliche automatisierte Verarbeitung ist die **lokale Ollama-Installation** die bessere Wahl: Mit 16-32 GB RAM lassen sich Modelle wie Llama 3.3 70B oder Mistral 7B unbegrenzt und kostenlos nutzen – die einzigen Kosten sind Strom und Hardware.

Alternative Cloud-Dienste für gehostete Open-Source-Modelle bieten bessere kostenlose Kontingente:
- **Modal Labs**: $30 kostenlose Credits pro Monat für GPU-Compute
- **Replicate**: $10 Startguthaben für neue Nutzer
- **Elestio**: Managed Ollama-Hosting ab stündlicher Abrechnung

## Google Gemini API: Drastische Kürzungen im Dezember 2025

Google hat am **7. Dezember 2025** die kostenlosen Kontingente erheblich reduziert. Gemini 2.5 Pro wurde für viele Accounts faktisch aus dem Free Tier entfernt, während Gemini 2.5 Flash von ~250 auf nur noch **~20 Requests pro Tag** beschränkt wurde. Die stabilsten kostenlosen Optionen sind nun die Lite-Modelle.

| Modell | Requests/Min | Requests/Tag | Tokens/Min |
|--------|-------------|--------------|------------|
| Gemini 2.5 Flash-Lite | 15-30 | 1.000-1.500 | 250.000 |
| Gemini 2.0 Flash-Lite | 15 | 1.500 | 1.000.000 |
| Gemini 2.5 Pro | 2-5 | 25-100* | 250.000 |

*Nach Dezember-Änderungen stark variabel je nach Account

Für den Zugang benötigt man nur einen Google-Account unter **aistudio.google.com**. Die API-Key-Erstellung dauert etwa 5 Minuten. Allerdings gilt eine **kritische Einschränkung für EU-Nutzer**: Die kostenlosen Services dürfen laut Terms of Service nicht für Endnutzer im EWR, der Schweiz oder dem Vereinigten Königreich verwendet werden – für EU-Apps ist der kostenpflichtige Tier zwingend erforderlich.

## OpenRouter bietet die großzügigsten kostenlosen Limits

OpenRouter hat sich als **beste Option** für automatisierte Systeme herauskristallisiert. Nach einer einmaligen Einzahlung von $10 erhält man dauerhaften Zugang zu über **30 komplett kostenlosen Modellen** mit 1.000 Requests pro Tag. Ohne Einzahlung sind immerhin 50 tägliche Requests möglich.

Die verfügbaren kostenlosen Modelle sind beeindruckend leistungsfähig:
- **Meta Llama 3.3 70B** – 131K Kontext, hervorragend für Zusammenfassungen
- **DeepSeek R1 0528** – 671B Parameter, 164K Kontext
- **Qwen3 Coder 480B** – 262K Kontext für längere Dokumente
- **Google Gemma 3 27B** – kompakt und schnell
- **OpenAI GPT-OSS 120B** – Open-Source-Alternative

Die Plattform erhebt 5,5% Gebühren auf Credit-Käufe, aber bei kostenlosen Modellen fallen keine weiteren Kosten an. Der einzige Nachteil: Prompts werden bei kostenlosen Endpunkten für Modellverbesserungen geloggt.

## Groq Cloud überzeugt durch Geschwindigkeit

Groq bietet ein **echtes kostenloses Tier ohne Kreditkarte** mit bis zu 14.400 Requests pro Tag für ausgewählte Modelle. Die Besonderheit ist die **extrem schnelle Inferenz** durch proprietäre LPU-Hardware mit 300-1.000 Tokens pro Sekunde – ideal für Batch-Verarbeitung vieler Artikel.

| Modell | Free Requests/Tag | Tokens/Min |
|--------|------------------|------------|
| Llama 3.3 70B | 14.400 | 6.000 |
| Llama Guard 4 | 14.400 | 15.000 |
| GPT-OSS 120B | 1.000 | 8.000 |
| Qwen3 32B | 1.000 | 6.000 |

Für 50-100 Artikel täglich ist Groq vollkommen ausreichend. Die Geschwindigkeit ermöglicht es, alle Artikel innerhalb weniger Minuten zu verarbeiten, während andere APIs mehrere Stunden benötigen würden.

## Weitere Alternativen im Überblick

**Mistral API** bietet laut Community-Berichten bis zu **1 Milliarde Tokens pro Monat** kostenlos, wobei konkrete Limits nicht öffentlich dokumentiert sind. Der EU-Sitz gewährleistet GDPR-Konformität. Aktuell ist das Devstral 2-Modell (123B Parameter) als Promotion komplett kostenlos – ideal für komplexere Zusammenfassungen.

**Together.ai** hat sein kostenloses Tier im Juli 2025 eingestellt und erfordert nun mindestens $5 Credit-Kauf. Die Plattform bleibt mit über 200 Open-Source-Modellen attraktiv, aber für rein kostenlosen Betrieb ungeeignet.

**Anthropic Claude** bietet nur $10 pro Monat an kostenloser API-Nutzung mit strengen Rate Limits (5 RPM, 20.000 TPM). Bei Claude-Preisen von $3-15 pro Million Output-Tokens reicht das für maximal 3.000-5.000 Zusammenfassungen – für täglichen Betrieb unzureichend.

**Hugging Face** gewährt monatliche Free Credits für die Inference API, die jedoch für 50-100 Artikel täglich nicht ausreichen. Der PRO-Plan ($9/Monat) bietet 20x mehr Credits und lohnt sich als Backup-Option.

## Empfehlung für 50-100 Artikel täglich

Für ein Daily-Briefing-System mit Zusammenfassung und Priorisierung von 50-100 Nachrichtenartikeln empfiehlt sich eine **Multi-Provider-Strategie**:

| Priorität | Anbieter | Modell | Tägliches Limit | Kosten |
|-----------|----------|--------|-----------------|--------|
| Primär | OpenRouter | Llama 3.3 70B | 1.000 Requests | $0 (nach $10 einmalig) |
| Backup | Groq | Llama 3.1 8B | 14.400 Requests | $0 |
| Fallback | Mistral | Devstral 2 | ~33 Mio. Tokens | $0 |

Diese Kombination bietet **mehrere tausend kostenlose Requests täglich** – weit mehr als für 50-100 Artikel benötigt. Bei durchschnittlich 2.000 Tokens pro Artikel (Input + Output) und 100 Artikeln täglich werden etwa 200.000 Tokens verbraucht, was problemlos in allen drei Free Tiers bleibt.

Für die höchste **Zusammenfassungsqualität** eignet sich Llama 3.3 70B über OpenRouter. Für maximale **Geschwindigkeit** ist Groq mit Llama 3.1 8B optimal. Bei EU-Produktionsanwendungen sollte Mistral als primärer Anbieter gewählt werden, da keine rechtlichen Einschränkungen wie bei Google Gemini existieren.

## Wichtige Risiken und Einschränkungen

Alle kostenlosen Tiers können **jederzeit gekürzt werden** – Google hat dies im Dezember 2025 ohne Vorwarnung um 92% getan. Produktionskritische Systeme sollten daher immer mehrere Provider als Fallback implementieren und ein kleines Budget für Notfälle einplanen.

Bei kostenlosen Endpunkten werden Prompts typischerweise für Modelltraining verwendet. Für sensible Nachrichteninhalte sollte dies berücksichtigt werden. Zudem bieten Free Tiers keine SLAs – Ausfälle oder Drosselungen während Peak-Zeiten sind möglich.

## Fazit

Die beste Strategie für ein kostenloses Daily-Briefing-System kombiniert **OpenRouter** als Hauptanbieter für Qualität, **Groq** für Geschwindigkeit und Volumen, sowie **Mistral** als EU-konformen Fallback. Mit dieser Konfiguration lassen sich problemlos 100+ Artikel täglich verarbeiten, ohne einen Cent zu zahlen – vorausgesetzt, man akzeptiert die Einschränkungen bezüglich Datenschutz und Verfügbarkeit.
