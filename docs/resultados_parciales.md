# Resultados parciales del banco de evaluación

Generado automáticamente el 2026-09-28 16:41 UTC con `python -m regspec.evaluacion.informe`. 1 repetición por condición; el banco sigue corriendo según el cupo gratuito de Groq.

## Promedio por condición

| Modelo | Condición | Spec válida | F1 campos | Campos exactos | Especificidad | Sensibilidad | Detección reglas | Exact. balanceada | Iteraciones | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| openai/gpt-oss-120b | LLM, 1 intento | 0.50 | 1.00 | 0.98 | 0.50 | 0.50 | 0.50 | 0.50 | 1.0 | 7.850 |
| openai/gpt-oss-120b | LLM + verificador (sin parser) | 0.50 | 0.50 | 0.50 | 0.50 | 0.50 | 0.50 | 0.50 | 1.5 | 8.528 |
| openai/gpt-oss-120b | Sistema sin evidencia | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.0 | 6.010 |
| openai/gpt-oss-120b | Sistema sin ejecución | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.0 | 6.010 |
| openai/gpt-oss-120b | Sistema completo | 1.00 | 1.00 | 0.98 | 1.00 | 0.97 | 0.92 | 0.99 | 2.5 | 11.914 |
| — | Parser sin IA | 1.00 | 1.00 | 0.90 | 0.60 | 0.42 | 0.00 | 0.51 | 0.0 | 0 |

## Exactitud balanceada por manual

| Manual | LLM, 1 intento (openai/gpt-oss-120b) | LLM + verificador (sin parser) (openai/gpt-oss-120b) | Sistema sin evidencia (openai/gpt-oss-120b) | Sistema sin ejecución (openai/gpt-oss-120b) | Sistema completo (openai/gpt-oss-120b) | Parser sin IA |
|---|---|---|---|---|---|---|
| m1_retenciones | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.92 |
| m2_cuentas | 0.00 | 0.00 | – | – | 0.97 | 0.10 |
