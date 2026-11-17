import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    // Europe/Berlin ist die Zeitzone der Schule – Tests laufen bewusst in einer anderen Zone,
    // damit Zeitzonen-Fehler auffallen.
    env: { TZ: 'UTC' },
    testTimeout: 20_000,
  },
});
