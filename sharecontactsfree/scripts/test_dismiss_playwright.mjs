import { chromium, webkit } from "playwright";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = "file://" + path.join(__dirname, "test_dismiss_page.html");

const html = `<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/css/materialize.min.css">
  <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
  <style>
    #app .shared-groups-table td:not(.shared-col-actions) { overflow: hidden; }
    #app .shared-col-actions { text-align: right; z-index: 2; position: relative; }
    #app .shared-action-buttons { display: inline-flex; gap: 8px; }
    #app .shared-import-progress-track { height: 8px; background: #eceff1; width: 100%; }
    #app .shared-import-progress-fill { height: 100%; width: 80%; background: teal; }
    #app .shared-col-status { min-width: 140px; position: relative; }
    #log { white-space: pre-wrap; background: #f5f5f5; padding: 8px; margin-top: 12px; }
  </style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/js/materialize.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/vue@2.6.11"></script>
</head>
<body>
<div id="app" class="container">
  <table class="striped shared-groups-table">
    <tbody>
      <tr v-for="(group, index) in sharedGroups" :key="index">
        <td>{{ group.name }}</td>
        <td class="shared-col-status">
          <div class="shared-import-progress">
            <div class="shared-import-progress-track"><div class="shared-import-progress-fill"></div></div>
          </div>
        </td>
        <td class="shared-col-actions">
          <div class="shared-action-buttons">
            <button type="button" class="btn waves-effect waves-light shared-action-retry" :data-retry-shared-group-index="index">Retry</button>
            <button type="button" class="btn waves-effect waves-light red lighten-1 shared-action-dismiss" :data-dismiss-shared-row-index="index">Dismiss</button>
          </div>
        </td>
      </tr>
    </tbody>
  </table>
  <pre id="log"></pre>
</div>
<script>
function log(msg){ document.getElementById('log').textContent += msg + '\\n'; }
window.__appVm = new Vue({
  el: '#app',
  data: { sharedGroups: [{ name: 'AL South', owner: 'owner@test.com', resourceName: 'recovered/x', shareId: 'abc' }] },
  mounted(){
    this.bindSharedGroupsTableActions();
  },
  methods: {
    bindSharedGroupsTableActions(){
      if (this._bound) return;
      const appRoot = document.getElementById('app');
      const vm = this;
      appRoot.addEventListener('click', function(event){
        const dismissBtn = event.target.closest('[data-dismiss-shared-row-index]');
        if (dismissBtn && appRoot.contains(dismissBtn)){
          event.preventDefault();
          event.stopPropagation();
          const index = Number(dismissBtn.getAttribute('data-dismiss-shared-row-index'));
          log('delegation dismiss index=' + index);
          vm.dismissSharedRecipientGroup(index);
          return;
        }
        const retryBtn = event.target.closest('[data-retry-shared-group-index]');
        if (retryBtn && appRoot.contains(retryBtn)){
          log('delegation retry');
        }
      }, true);
      this._bound = true;
    },
    dismissSharedRecipientGroup(index){
      log('dismissSharedRecipientGroup(' + index + ')');
    }
  }
});
</script>
</body>
</html>`;

import fs from "fs";
fs.writeFileSync(path.join(__dirname, "test_dismiss_page.html"), html);

for (const browserType of [chromium, webkit]) {
  const name = browserType.name();
  const browser = await browserType.launch();
  const page = await browser.newPage();
  await page.goto(htmlPath);
  await page.waitForSelector(".shared-action-dismiss");
  await page.click(".shared-action-dismiss");
  const log = await page.textContent("#log");
  console.log(name + " log:", JSON.stringify(log));
  await browser.close();
}
