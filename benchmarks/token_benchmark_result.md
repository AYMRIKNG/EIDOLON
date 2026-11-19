# Benchmark prédictif — Gemini Live vs Low token

- Prompt système + outils JARVIS : **8 920 tokens** (mesuré (prompt 3544 + outils 5376))
- Phrase utilisateur 4 s, réponse 6 s, audio 25 tok/s, plafond de contexte 32 000 tokens

| Session | Live (entrée / sortie tokens) | Live coût | Live + micro continu | Low token (entrée / sortie) | Low token coût | Économie |
|---|---|---|---|---|---|---|
| 10 tours | 101 450 / 1 830 | 0.123 $ | 0.206 $ | 92 216 / 330 | 0.0285 $ | **-77 %** |
| 30 tours | 379 350 / 5 490 | 0.594 $ | 1.292 $ | 286 551 / 990 | 0.0884 $ | **-85 %** |
| 100 tours | 2 133 060 / 18 300 | 4.587 $ | 6.868 $ | 1 070 669 / 3 300 | 0.3295 $ | **-93 %** |

**Projection mensuelle** (1 session de 30 tours / jour) : Live ≈ **17.83 $**, Live micro continu ≈ **38.76 $**, Low token ≈ **2.65 $**.