import fs from "fs/promises";

const destPath = new URL("../dist", import.meta.url);

// Only delete if the directory exists.
let destExists = fs.access(destPath, fs.constants.F_OK)
    .then(() => true)
    .catch(() => false);

if (destExists == true) {
    await fs.rm(destPath, { recursive: true });
}
