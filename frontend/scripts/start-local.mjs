import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { join } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const standalone = join(root, ".next", "standalone");
const server = join(standalone, "server.js");
if (!existsSync(server)) throw new Error("Run npm run build before npm start.");
// Ordinary file I/O also works in Windows workspaces where native directory-copy APIs are restricted.
function copyAssets(source, destination) {
  mkdirSync(destination, { recursive: true });
  for (const entry of readdirSync(source, { withFileTypes: true })) {
    const from = join(source, entry.name);
    const to = join(destination, entry.name);
    if (entry.isDirectory()) copyAssets(from, to);
    else if (entry.isFile()) writeFileSync(to, readFileSync(from));
  }
}
copyAssets(join(root, ".next", "static"), join(standalone, ".next", "static"));
if (existsSync(join(root, "public"))) copyAssets(join(root, "public"), join(standalone, "public"));
process.env.HOSTNAME = "127.0.0.1";
process.env.PORT ||= "3000";
await import(pathToFileURL(server).href);
