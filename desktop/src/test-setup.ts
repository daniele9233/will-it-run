// Tests assert on English text whatever the language of the machine running them:
// the interface reads its language from localStorage before anything else loads.
const store = new Map<string, string>([["agentfit.lang", "en"]]);

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  },
});
