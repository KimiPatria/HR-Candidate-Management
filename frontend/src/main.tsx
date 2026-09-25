import { Classes } from "@blueprintjs/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";

// Blueprint's stylesheets must load before ours - index.css overrides a handful of
// its variables and owns the layout shells Blueprint has no component for.
import "normalize.css/normalize.css";
import "@blueprintjs/core/lib/css/blueprint.css";
// blueprint-icons.css is deliberately NOT imported: it only supplies the @font-face for
// the legacy icon font, and every icon here is a React SVG component (`icon="..."` on
// Blueprint components). Importing it would ship ~1.6MB of unused .woff/.ttf/.eot/.svg.
import "./index.css";

// Dark theme goes on <body>, not on an inner wrapper: Blueprint renders overlays
// (dialogs, popovers, toasts) into portals attached to document.body, and those would
// come out light if the class only covered the app subtree.
document.body.classList.add(Classes.DARK);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
