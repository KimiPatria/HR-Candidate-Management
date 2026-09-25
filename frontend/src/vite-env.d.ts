/// <reference types="vite/client" />

// Pulls in Vite's ambient types, which is what declares the `?url` import suffix used by
// localVoice.ts to get a servable URL for the AudioWorklet module. A worklet cannot be
// bundled into the main chunk - the browser fetches it as its own script on the audio
// thread - so it has to be referenced by URL rather than imported normally.
