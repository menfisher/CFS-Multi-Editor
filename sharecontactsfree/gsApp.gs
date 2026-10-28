function resetApp(){
    let scriptProps = PropertiesService.getScriptProperties()
    console.log(scriptProps.getProperties())
    scriptProps.deleteAllProperties()
}

function log(){
    let scriptProps = PropertiesService.getScriptProperties()
    console.log(JSON.parse(JSON.stringify((scriptProps.getProperties()))))
}

function repairSharedStoreAccess(recipientEmail){
    let sheet = getSharedSheet_(true)
    let ss = sheet.getParent()
    let email = recipientEmail ? String(recipientEmail).trim().toLowerCase() : ""
    let info = {
        sheetId: ss.getId(),
        sheetUrl: ss.getUrl(),
        recipientEmail: email || null,
        addViewerOk: null,
        error: null
    }
    if (email){
        try {
            ss.addViewer(email)
            info.addViewerOk = true
        } catch (e) {
            info.addViewerOk = false
            info.error = String((e && e.message) ? e.message : e)
        }
    }
    Logger.log(JSON.stringify(info))
    return info
}

const SHARED_STORE_CLEANUP_HANDLER_ = "runScheduledSharedStoreCleanup"
const SHARED_STORE_CLEANUP_STATUS_KEY_ = "shared_store_cleanup_last_run"

function collectActiveShareIds_(){
    let props = PropertiesService.getScriptProperties().getProperties()
    let active = {}
    Object.keys(props).forEach(key=>{
        let raw = props[key]
        if (!raw || raw[0] !== "{"){
            return
        }
        let data
        try {
            data = JSON.parse(raw)
        } catch (e) {
            return
        }
        let sharedGroups = Array.isArray(data.sharedGroups) ? data.sharedGroups : []
        sharedGroups.forEach(group=>{
            let shareId = group && group.shareId ? String(group.shareId) : ""
            if (shareId){
                active[shareId] = true
            }
        })
    })
    return active
}

function cleanupSharedContactsStoreNow(){
    let runMeta = {
        at: new Date().toISOString(),
        ok: false,
        removedRows: 0,
        keptRows: 0,
        activeShareIds: 0,
        error: ""
    }
    try {
        let activeShareIds = collectActiveShareIds_()
        let sheet = getSharedSheet_(true)
        let values = sheet.getDataRange().getValues()
        if (!values.length){
            runMeta.ok = true
            runMeta.activeShareIds = Object.keys(activeShareIds).length
            recordSharedStoreCleanupStatus_(runMeta)
            Logger.log(JSON.stringify(runMeta))
            return runMeta
        }

        let kept = [values[0]]
        let removedRows = 0
        for (let i = 1; i < values.length; i++){
            let row = values[i]
            let shareId = String(row[0] || "")
            if (shareId && activeShareIds[shareId]){
                kept.push(row)
            } else {
                removedRows++
            }
        }

        if (removedRows > 0){
            sheet.clearContents()
            sheet.getRange(1, 1, kept.length, kept[0].length).setValues(kept)
        }

        runMeta.ok = true
        runMeta.removedRows = removedRows
        runMeta.keptRows = kept.length - 1
        runMeta.activeShareIds = Object.keys(activeShareIds).length
        recordSharedStoreCleanupStatus_(runMeta)
        Logger.log(JSON.stringify(runMeta))
        return runMeta
    } catch (e) {
        runMeta.error = String((e && e.message) ? e.message : e)
        recordSharedStoreCleanupStatus_(runMeta)
        Logger.log(JSON.stringify(runMeta))
        throw e
    }
}

function runScheduledSharedStoreCleanup(){
    return cleanupSharedContactsStoreNow()
}

function removeSharedStoreCleanupTriggers(){
    let removed = 0
    ScriptApp.getProjectTriggers().forEach(trigger=>{
        if (trigger.getHandlerFunction() === SHARED_STORE_CLEANUP_HANDLER_){
            ScriptApp.deleteTrigger(trigger)
            removed++
        }
    })
    return {removed}
}

function installSharedStoreCleanupTrigger(){
    removeSharedStoreCleanupTriggers()
    ScriptApp.newTrigger(SHARED_STORE_CLEANUP_HANDLER_)
        .timeBased()
        .everyDays(1)
        .atHour(3)
        .create()
    return {status: "ok", handler: SHARED_STORE_CLEANUP_HANDLER_, schedule: "daily around 3 AM"}
}

function recordSharedStoreCleanupStatus_(status){
    PropertiesService.getScriptProperties().setProperty(
        SHARED_STORE_CLEANUP_STATUS_KEY_,
        JSON.stringify(status || {})
    )
}

function getSharedStoreCleanupStatus(){
    let raw = PropertiesService.getScriptProperties().getProperty(SHARED_STORE_CLEANUP_STATUS_KEY_)
    let lastRun = null
    if (raw){
        try {
            lastRun = JSON.parse(raw)
        } catch (e) {
            lastRun = {at: "", ok: false, error: "Status parse error"}
        }
    }
    let hasTrigger = false
    ScriptApp.getProjectTriggers().forEach(trigger=>{
        if (trigger.getHandlerFunction() === SHARED_STORE_CLEANUP_HANDLER_){
            hasTrigger = true
        }
    })
    let result = {
        handler: SHARED_STORE_CLEANUP_HANDLER_,
        hasTrigger,
        lastRun
    }
    Logger.log(JSON.stringify(result))
    return result
}

function repairSharedStoreAccessForAllRecipients() {
  const activeShareIds = collectActiveShareIds_()
  const sheet = getSharedSheet_(true)
  const ss = sheet.getParent()
  const scriptProps = PropertiesService.getScriptProperties().getProperties()
  const recipients = {}
  Object.keys(scriptProps).forEach(key=>{
    const normalized = String(key || "").trim().toLowerCase()
    if (!normalized || normalized.indexOf("@") < 0){
      return
    }
    let data
    try {
      data = JSON.parse(scriptProps[key] || "{}")
    } catch (e) {
      return
    }
    const sharedGroups = Array.isArray(data.sharedGroups) ? data.sharedGroups : []
    const hasActive = sharedGroups.some(group=>group && group.shareId && activeShareIds[String(group.shareId)])
    if (hasActive){
      recipients[normalized] = true
    }
  })
  const emails = Object.keys(recipients)
  const failed = []
  emails.forEach(email=>{
    try {
      ss.addViewer(email)
    } catch (e) {
      failed.push(email)
    }
  })
  const result = {total: emails.length, failed}
  Logger.log(JSON.stringify(result))
  return result
}
