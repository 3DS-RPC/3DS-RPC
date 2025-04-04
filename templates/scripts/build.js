import autoprefixer from "autoprefixer";
import fs from "fs/promises";
import path from "path";
import postcss from "postcss";
import prettier from "prettier";
import pug from "pug";
import * as sass from "sass";
import url from "url";

// A few common paths used throughout our script.
// We expect ./scripts/build.js, thus our base directory `.` is `../..`.
const basePath = url.fileURLToPath(import.meta.resolve("../"));
const srcPath = path.resolve(basePath, "./src");
const staticPath = path.resolve(basePath, "../static");

////////////
// Assets //
////////////
console.log(`### INFO: Copying static assets...`);
const sourceAssets = path.resolve(srcPath, "./assets");
const staticAssets = path.resolve(staticPath, "./assets");
await fs.cp(sourceAssets, staticAssets, { recursive: true });

console.log(`### INFO: Copying static JavaScript...`);
const sourceScripts = path.resolve(srcPath, "./js");
const staticScripts = path.resolve(staticPath, "./js");
await fs.cp(sourceScripts, staticScripts, { recursive: true });

//////////
// SCSS //
//////////
console.log(`### INFO: Rendering SCSS...`);
const sourceStylePath = path.resolve(srcPath, "./scss/styles.scss");
const compiledStyle = sass.compile(sourceStylePath, {
    // We enable Node.js package resolution for usage with Bootstrap.
    importers: [new sass.NodePackageImporter()],

    // TODO: Bootstrap does not currently utilize the Sass module system.
    // We sadly must silence the deprecation for @import,
    // alongside Bootstrap's own usage of @import (`quietDeps`).
    //
    // For more information regarding the @import deprecation:
    // https://sass-lang.com/documentation/breaking-changes/import/
    silenceDeprecations: ["import"],
    quietDeps: true,
});

const processedStyle = await postcss()
    .use(autoprefixer)
    .process(compiledStyle.css, { from: "styles.css", to: "styles.css" });
// Log processing warnings if necessary.
processedStyle.warnings().forEach((warn) => {
    console.warn(warn.toString());
});

// Write and saved our processed CSS.
const staticCssDir = path.resolve(staticPath, "./css");
await fs.mkdir(staticCssDir, { recursive: true });

const staticStylePath = path.resolve(staticCssDir, "./styles.css");
await fs.writeFile(staticStylePath, processedStyle.css, "utf8");

////////////////////
// HTML Templates //
////////////////////
const sourceTemplateDir = path.resolve(srcPath, "./pug/pages");
const sourceTemplates = await fs.readdir(sourceTemplateDir);
// Ensure our output directory exists.
const destTemplateDir = path.resolve(basePath, "./dist");
await fs.mkdir(destTemplateDir, { recursive: true });

for (const sourceTemplatePath of sourceTemplates) {
    // Ensure we only handle *.pug files.
    const templateName = path.basename(sourceTemplatePath);
    if (!templateName.endsWith(".pug")) {
        continue;
    }

    // We output from src/pug/pages/*.pug to dist/*.html.
    const outputName = templateName.replace(".pug", ".html");
    const sourcePath = path.resolve(sourceTemplateDir, templateName);
    const destPath = path.resolve(destTemplateDir, outputName);
    console.log(`### INFO: Rendering ${sourcePath} to ${destPath}...`);

    // First, render via Pug.
    const renderedHtml = pug.renderFile(sourcePath, {
        doctype: "html",
        filename: templateName,
        basedir: sourceTemplateDir,
    });

    // Next, beautify its source.
    const prettifiedHtml = await prettier.format(renderedHtml, {
        printWidth: 1000,
        tabWidth: 4,
        singleQuote: true,
        proseWrap: "preserve",
        endOfLine: "lf",
        parser: "html",
        htmlWhitespaceSensitivity: "ignore",
    });

    // We're done!
    await fs.writeFile(destPath, prettifiedHtml, "utf-8");
}
