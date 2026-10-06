import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { installApiClient } from "./lib/api-client";
import "./index.css";

// Must run before any component mounts: adds the session cookie to every
// backend call and intercepts 401 so an expired session surfaces instead of
// being swallowed into empty data by each hook's own catch block.
installApiClient();

createRoot(document.getElementById("root")!).render(<App />);
