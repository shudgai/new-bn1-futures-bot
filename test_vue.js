const compiler = require('@vue/compiler-dom');
const fs = require('fs');
const html = fs.readFileSync('web/index.html', 'utf8');
const match = html.match(/<div id="app">([\s\S]*?)<\/div>\s*<\/div>\s*<script>/);
// The above regex might be wrong depending on how many closing divs. Let's just find the #app block
const appStart = html.indexOf('<div id="app">');
const scriptStart = html.indexOf('<script>', appStart);
const template = html.substring(appStart, scriptStart);

try {
  compiler.compile(template);
  console.log("Template is valid!");
} catch (e) {
  console.error("Vue compile error:", e);
}
