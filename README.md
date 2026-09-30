# Manual de procesos

Página cifrada (AES-GCM + PBKDF2). Sin la contraseña el contenido no se puede leer.

- El contenido sale de Google Docs y se actualiza solo una vez por semana (lunes 7:00 CDMX) (GitHub Actions).
- La lista de documentos (`DOCS_JSON`) y la contraseña (`CLAVE`) son secretos del repositorio: no están en el código.
- Para actualizar ya: pestaña **Actions** → "Actualizar manual desde Google Docs" → **Run workflow**.
