import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { installApiClient } from "./lib/api-client";
import { installClientLogCapture } from "./lib/client-log-capture";
import "./index.css";

// Must run before any component mounts: adds the session cookie to every
// backend call and intercepts 401 so an expired session surfaces instead of
// being swallowed into empty data by each hook's own catch block.
installApiClient();

// Browser-console log sender. Dormant until an admin turns capture on; no-ops
// under SSR/tests. Errors + warnings only by default.
if (typeof window !== "undefined") installClientLogCapture();

createRoot(document.getElementById("root")!).render(<App />);
