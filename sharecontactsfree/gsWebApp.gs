function getPageUrl(pageName){
    let url = ScriptApp.getService().getUrl()
    if (pageName) {
        url += `?p=${pageName}`
    }
    return url
}

function include(filename){
    return HtmlService.createTemplateFromFile(filename).evaluate().getContent()
}

const SHARED_CONTACTS_HEADER_ = ["shareId","owner","groupName","groupResourceName","memberIndex","memberJson","createdAt"]
const SYNC_MAPPINGS_HEADER_ = [
    "shareId",
    "owner",
    "groupResourceName",
    "recipientEmail",
    "ownerPersonId",
    "recipientPersonId",
    "lastHash",
    "lastPhotoHash",
    "lastSync",
    "ownerEtag"
]
const SYNC_WRITABLE_FIELDS_ = [
    "addresses",
    "biographies",
    "birthdays",
    "emailAddresses",
    "names",
    "nicknames",
    "organizations",
    "phoneNumbers",
    "relations",
    "urls",
    "userDefined"
]
const SYNC_PERSON_FIELDS_ = [
    "addresses",
    "biographies",
    "birthdays",
    "emailAddresses",
    "names",
    "nicknames",
    "organizations",
    "phoneNumbers",
    "photos",
    "relations",
    "urls",
    "userDefined"
].join(",")
const RECIPIENT_IMPORT_BATCH_SIZE_ = 60
const RECIPIENT_IMPORT_MAX_OPS_PER_LOAD_ = 25
const RECIPIENT_IMPORT_TIME_BUDGET_MS_ = 25000
const RECIPIENT_IMPORT_MAX_RETRIES_ = 5
const RECIPIENT_IMPORT_INCLUDE_PHOTOS_ = true
const RECIPIENT_IMPORT_PHOTO_BATCH_SIZE_ = 8
const OWNER_SYNC_BATCH_SIZE_ = 90
const OWNER_SYNC_MAX_RETRIES_ = 5
const OWNER_FETCH_BATCH_SIZE_ = 15
const OWNER_SYNC_TIME_BUDGET_MS_ = 50000
const OWNER_SYNC_AUTO_RESUME_MAX_AGE_MS_ = 10 * 60 * 1000

function isBandwidthQuotaError_(error){
    return String((error && error.message) ? error.message : error)
        .toLowerCase()
        .includes("bandwidth quota exceeded")
}

function fetchJson_(api, params){
    let response = UrlFetchApp.fetch(api, params)
    let code = response.getResponseCode()
    let text = response.getContentText()
    if (code < 200 || code >= 300){
        throw new Error(`People API error (${code}) for ${api}: ${text}`)
    }
    return JSON.parse(text)
}

function getOAuthConfig_(){
    let props = PropertiesService.getScriptProperties()
    return {
        clientId: props.getProperty("OAUTH_CLIENT_ID"),
        clientSecret: props.getProperty("OAUTH_CLIENT_SECRET"),
    }
}

function getRecipientService_(userId){
    let {clientId, clientSecret} = getOAuthConfig_()
    if (!clientId || !clientSecret){
        throw new Error("OAuth client is not configured. Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET in Script Properties.")
    }
    return OAuth2.createService("contacts-" + userId)
        .setAuthorizationBaseUrl("https://accounts.google.com/o/oauth2/auth")
        .setTokenUrl("https://oauth2.googleapis.com/token")
        .setClientId(clientId)
        .setClientSecret(clientSecret)
        .setCallbackFunction("authCallback")
        .setPropertyStore(PropertiesService.getScriptProperties())
        .setCache(CacheService.getScriptCache())
        .setLock(LockService.getScriptLock())
        .setScope([
            "https://www.googleapis.com/auth/contacts",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile"
        ])
        .setParam("access_type", "offline")
        .setParam("prompt", "consent")
}

function fetchWithService_(service, api, params){
    let headers = (params && params.headers) ? params.headers : {}
    headers.Authorization = "Bearer " + service.getAccessToken()
    let merged = Object.assign({}, params || {}, {headers})
    return UrlFetchApp.fetch(api, merged)
}

function getRecipientEmail_(){
    let email = Session.getActiveUser().getEmail()
    if (email){
        return email
    }
    try {
        return getProfile().email
    } catch (e) {
        return null
    }
}

function getConnectUrl(){
    let email = getRecipientEmail_()
    if (!email){
        throw new Error("Unable to determine your email. Please sign in with Google.")
    }
    let service = getRecipientService_(email)
    if (service.hasAccess()){
        return {authorized: true}
    }
    return {authorized: false, url: service.getAuthorizationUrl()}
}

function authCallback(request){
    let email = getRecipientEmail_()
    if (!email){
        return HtmlService.createHtmlOutput("Unable to determine your email. Please close this window and try again.")
    }
    let service = getRecipientService_(email)
    let authorized = service.handleCallback(request)
    if (authorized){
        return HtmlService.createHtmlOutput("Connected. You can close this window.")
    }
    return HtmlService.createHtmlOutput("Access denied. You can close this window.")
}

function isRecipientConnected_(){
    let email = getRecipientEmail_()
    if (!email){
        return false
    }
    let service = getRecipientService_(email)
    return service.hasAccess()
}

function getSharedSheet_(createIfMissing){
    let props = PropertiesService.getScriptProperties()
    let sheetId = props.getProperty("sharedSheetId")
    let sheet
    if (sheetId){
        try {
            let ss = SpreadsheetApp.openById(sheetId)
            sheet = ss.getSheetByName("SharedContacts") || ss.getSheets()[0]
        } catch (e) {
            sheet = null
        }
    }
    if (!sheet && !createIfMissing){
        throw new Error("SharedContactsStore is not accessible. Ask the owner to share the sheet with you.")
    }
    if (!sheet){
        let ss = SpreadsheetApp.create("SharedContactsStore")
        sheet = ss.getActiveSheet()
        sheet.setName("SharedContacts")
        props.setProperty("sharedSheetId", ss.getId())
    }
    // Avoid per-recipient Drive ACL churn by granting link-view access once.
    try {
        DriveApp.getFileById(sheet.getParent().getId())
            .setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW)
        props.setProperty("sharedSheetLinkAccessEnabled", "true")
    } catch (e) {
        // Leave existing permissions as-is if sharing cannot be updated.
    }
    if (sheet.getLastRow() === 0){
        sheet.appendRow(SHARED_CONTACTS_HEADER_)
    }
    return sheet
}

function ensureSharedSheetAccess_(emails, options){
    options = options || {}
    let trustedAccessible = options.trustedAccessible || {}
    let props = PropertiesService.getScriptProperties()
    let linkAccessEnabled = props.getProperty("sharedSheetLinkAccessEnabled") === "true"
    emails = Array.from(new Set((emails || []).map(email=>String(email || "").trim().toLowerCase()).filter(Boolean)))
    if (!emails.length){
        return {granted: 0, failedEmails: []}
    }
    let file
    try {
        let sheet = getSharedSheet_(true)
        file = DriveApp.getFileById(sheet.getParent().getId())
    } catch (e) {
        return {granted: 0, failedEmails: emails}
    }
    let hasLinkViewAccess = false
    try {
        let sharingAccess = file.getSharingAccess()
        hasLinkViewAccess = (
            sharingAccess === DriveApp.Access.ANYONE ||
            sharingAccess === DriveApp.Access.ANYONE_WITH_LINK
        )
    } catch (e) {
        hasLinkViewAccess = false
    }
    if (hasLinkViewAccess){
        return {granted: emails.length, failedEmails: []}
    }
    let viewerSet = {}
    let editorSet = {}
    try {
        file.getViewers().forEach(user=>{
            let email = String(user.getEmail() || "").trim().toLowerCase()
            if (email){
                viewerSet[email] = true
            }
        })
    } catch (e) {}
    try {
        file.getEditors().forEach(user=>{
            let email = String(user.getEmail() || "").trim().toLowerCase()
            if (email){
                editorSet[email] = true
            }
        })
    } catch (e) {}
    let granted = 0
    let failedEmails = []
    emails.forEach(email=>{
        if (trustedAccessible[email]){
            granted++
            return
        }
        if (viewerSet[email] || editorSet[email]){
            granted++
            return
        }
        try {
            file.addViewer(email)
            granted++
            viewerSet[email] = true
        } catch (e) {
            // Re-check after failure to avoid false warnings when access already exists.
            try {
                if (hasLinkViewAccess || linkAccessEnabled){
                    granted++
                    return
                }
                let refreshedViewers = file.getViewers()
                    .map(user=>String(user.getEmail() || "").trim().toLowerCase())
                    .filter(Boolean)
                if (refreshedViewers.includes(email)){
                    granted++
                    viewerSet[email] = true
                    return
                }
            } catch (ignored) {}
            failedEmails.push(email)
        }
    })
    return {granted, failedEmails}
}

function writeSharedContacts_(shareId, owner, groupName, groupResourceName, membersData){
    if (!membersData || !membersData.length){
        return
    }
    let sheet = getSharedSheet_(true)
    let rows = []
    let createdAt = new Date().toISOString()
    membersData.forEach((memberData, index)=>{
        rows.push([
            shareId,
            owner,
            groupName,
            groupResourceName,
            index,
            JSON.stringify(memberData),
            createdAt
        ])
    })
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows)
}

function deleteSharedContactsByGroup_(owner, groupResourceName){
    let sheet = getSharedSheet_(true)
    let values = sheet.getDataRange().getValues()
    let kept = [values[0]]
    for (let i = 1; i < values.length; i++){
        let row = values[i]
        if (!(row[1] === owner && row[3] === groupResourceName)){
            kept.push(row)
        }
    }
    sheet.clearContents()
    if (kept.length){
        sheet.getRange(1, 1, kept.length, kept[0].length).setValues(kept)
    } else {
        sheet.appendRow(SHARED_CONTACTS_HEADER_)
    }
}

function readSharedContacts_(shareId){
    if (!shareId){
        return []
    }
    let sheet = getSharedSheet_(false)
    let values = sheet.getDataRange().getValues()
    let members = []
    for (let i = 1; i < values.length; i++){
        let row = values[i]
        if (row[0] === shareId && row[5]){
            try {
                members.push(JSON.parse(row[5]))
            } catch (e) {}
        }
    }
    return members
}

function readSharedContactRows_(shareId){
    if (!shareId){
        return []
    }
    let sheet = getSharedSheet_(false)
    let values = sheet.getDataRange().getValues()
    let rows = []
    for (let i = 1; i < values.length; i++){
        let row = values[i]
        if (row[0] !== shareId || !row[5]){
            continue
        }
        try {
            rows.push({
                sheetRow: i + 1,
                memberIndex: Number(row[4]),
                memberData: JSON.parse(row[5])
            })
        } catch (e) {}
    }
    rows.sort((a, b)=>a.memberIndex - b.memberIndex)
    return rows
}

function deleteSharedContacts_(shareId){
    if (!shareId){
        return
    }
    let sheet = getSharedSheet_(true)
    let values = sheet.getDataRange().getValues()
    let kept = [values[0]]
    for (let i = 1; i < values.length; i++){
        if (values[i][0] !== shareId){
            kept.push(values[i])
        }
    }
    sheet.clearContents()
    if (kept.length){
        sheet.getRange(1, 1, kept.length, kept[0].length).setValues(kept)
    } else {
        sheet.appendRow(SHARED_CONTACTS_HEADER_)
    }
}

function getSyncMappingsSheet_(createIfMissing){
    let sharedSheet = getSharedSheet_(createIfMissing)
    let ss = sharedSheet.getParent()
    let sheet = ss.getSheetByName("SyncMappings")
    if (!sheet && !createIfMissing){
        return null
    }
    if (!sheet){
        sheet = ss.insertSheet("SyncMappings")
    }
    if (sheet.getLastRow() === 0){
        sheet.appendRow(SYNC_MAPPINGS_HEADER_)
    } else {
        // Backward-compatible schema upgrade: add ownerEtag column if missing.
        if (sheet.getLastColumn() < SYNC_MAPPINGS_HEADER_.length){
            sheet.getRange(1, 1, 1, SYNC_MAPPINGS_HEADER_.length).setValues([SYNC_MAPPINGS_HEADER_])
        } else {
            let header = sheet.getRange(1, 1, 1, SYNC_MAPPINGS_HEADER_.length).getValues()[0]
            if (header[SYNC_MAPPINGS_HEADER_.length - 1] !== "ownerEtag"){
                sheet.getRange(1, 1, 1, SYNC_MAPPINGS_HEADER_.length).setValues([SYNC_MAPPINGS_HEADER_])
            }
        }
    }
    return sheet
}

function loadSyncMappings_(shareId, owner, groupResourceName, recipientEmail){
    let sheet = getSyncMappingsSheet_(true)
    let values = sheet.getDataRange().getValues()
    let byOwnerId = {}
    let byOwnerIdAny = {}
    for (let i = 1; i < values.length; i++){
        let row = values[i]
        if (row[1] !== owner || row[3] !== recipientEmail || !row[4]){
            continue
        }
        let mapping = {
            rowNumber: i + 1,
            shareId: row[0],
            owner: row[1],
            groupResourceName: row[2],
            recipientEmail: row[3],
            ownerPersonId: row[4],
            recipientPersonId: row[5],
            lastHash: row[6] || "",
            lastPhotoHash: row[7] || "",
            lastSync: row[8] || "",
            ownerEtag: row[9] || ""
        }
        let ownerPersonId = row[4]
        let existingAny = byOwnerIdAny[ownerPersonId]
        if (!existingAny){
            byOwnerIdAny[ownerPersonId] = mapping
        } else {
            let existingTs = new Date(existingAny.lastSync || 0).getTime()
            let nextTs = new Date(mapping.lastSync || 0).getTime()
            let existingScore = isFinite(existingTs) ? existingTs : 0
            let nextScore = isFinite(nextTs) ? nextTs : 0
            if (nextScore >= existingScore){
                byOwnerIdAny[ownerPersonId] = mapping
            }
        }
        if (row[0] !== shareId || row[2] !== groupResourceName){
            continue
        }
        byOwnerId[ownerPersonId] = mapping
    }
    return {sheet, byOwnerId, byOwnerIdAny}
}

function hasOtherSyncMappingForOwnerPerson_(owner, recipientEmail, ownerPersonId, excludeShareId, excludeGroupResourceName){
    let sheet = getSyncMappingsSheet_(true)
    let values = sheet.getDataRange().getValues()
    for (let i = 1; i < values.length; i++){
        let row = values[i]
        if (row[1] !== owner || row[3] !== recipientEmail || row[4] !== ownerPersonId){
            continue
        }
        if (row[0] === excludeShareId && row[2] === excludeGroupResourceName){
            continue
        }
        if (row[5]){
            return true
        }
    }
    return false
}

function getSiblingSyncMappingsForOwnerPerson_(sheet, owner, recipientEmail, ownerPersonId, excludeRowNumber){
    let values = sheet.getDataRange().getValues()
    let out = []
    for (let i = 1; i < values.length; i++){
        let row = values[i]
        let rowNumber = i + 1
        if (excludeRowNumber && rowNumber === excludeRowNumber){
            continue
        }
        if (row[1] !== owner || row[3] !== recipientEmail || row[4] !== ownerPersonId){
            continue
        }
        out.push({
            rowNumber,
            shareId: row[0],
            owner: row[1],
            groupResourceName: row[2],
            recipientEmail: row[3],
            ownerPersonId: row[4],
            recipientPersonId: row[5],
            lastHash: row[6] || "",
            lastPhotoHash: row[7] || "",
            lastSync: row[8] || "",
            ownerEtag: row[9] || ""
        })
    }
    return out
}

function deleteSyncMappingRows_(sheet, rowNumbers){
    if (!rowNumbers || !rowNumbers.length){
        return
    }
    rowNumbers.sort((a, b) => b - a).forEach(rowNumber=>{
        sheet.deleteRow(rowNumber)
    })
}

function chunkArray_(arr, size){
    let chunks = []
    for (let i = 0; i < arr.length; i += size){
        chunks.push(arr.slice(i, i + size))
    }
    return chunks
}

function chunkResourceNamesByUrlLength_(resourceNames, baseApi, normalize=false, maxUrlLength=1800){
    let chunks = []
    let current = []
    let currentLength = baseApi.length
    let paramPrefix = "resourceNames="
    resourceNames.forEach(name=>{
        let value = normalize ? normalizePersonResourceName_(name) : name
        let encoded = encodeURIComponent(value)
        let extra = (current.length ? 1 : 0) + paramPrefix.length + encoded.length // +1 for '&'
        if (current.length && (currentLength + extra > maxUrlLength)){
            chunks.push(current)
            current = [value]
            currentLength = baseApi.length + paramPrefix.length + encoded.length
            return
        }
        current.push(value)
        currentLength += extra
    })
    if (current.length){
        chunks.push(current)
    }
    return chunks
}

function sanitizePersonData_(data){
    if (!data){
        return {}
    }
    let clean = JSON.parse(JSON.stringify(data))
    delete(clean.etag)
    delete(clean.resourceName)
    Object.keys(clean).forEach(key=>{
        let items = clean[key]
        if (!Array.isArray(items)){
            return
        }
        items.forEach((item, index)=>{
            if (clean[key][index].metadata){
                delete(clean[key][index].metadata)
            }
            if (clean[key][index].formattedType){
                delete(clean[key][index].formattedType)
            }
        })
    })
    return clean
}

function getPeopleDataBatch_(resourceNames, options){
    options = options || {}
    let out = {}
    if (!resourceNames || !resourceNames.length){
        return out
    }
    let token = ScriptApp.getOAuthToken()
    let baseApi = `https://people.googleapis.com/v1/people:batchGet?personFields=${encodeURIComponent(SYNC_PERSON_FIELDS_)}`
    let maxUrlLength = Number(options.maxUrlLength || 1800)
    if (!isFinite(maxUrlLength) || maxUrlLength < 1200){
        maxUrlLength = 1800
    }
    let skipCache = !!options.skipCache
    let chunks = chunkResourceNamesByUrlLength_(resourceNames, baseApi, false, maxUrlLength)
    chunks.forEach(chunk=>{
        let queue = [chunk]
        while (queue.length){
            let current = queue.shift()
            let query = current.map(name=>`resourceNames=${encodeURIComponent(name)}`).join("&")
            let api = `${baseApi}&${query}`
            let params = {
                headers: {
                    Authorization: 'Bearer ' + token,
                },
                muteHttpExceptions: true,
            }
            try {
                let data = fetchJson_(api, params)
                let responses = data.responses || []
                responses.forEach(response=>{
                    if (response.httpStatusCode && response.httpStatusCode !== 200){
                        return
                    }
                    let person = response.person
                    if (!person || !person.resourceName){
                        return
                    }
                    let clean = sanitizePersonData_(person)
                    out[person.resourceName] = clean
                    if (!skipCache){
                        setCachedPerson_(person.resourceName, clean)
                    }
                })
            } catch (e) {
                let message = String((e && e.message) ? e.message : e)
                let oversized = message.includes("URLFetch URL Length") || message.includes("Limit Exceeded: URLFetch URL Length")
                if (oversized && current.length > 1){
                    let mid = Math.ceil(current.length / 2)
                    queue.unshift(current.slice(mid))
                    queue.unshift(current.slice(0, mid))
                    continue
                }
                throw e
            }
        }
    })
    return out
}

function getRecipientEtagsBatchAs_(service, resourceNames){
    let out = {}
    if (!resourceNames || !resourceNames.length){
        return out
    }
    let fields = encodeURIComponent("metadata")
    let baseApi = `https://people.googleapis.com/v1/people:batchGet?personFields=${fields}`
    let chunks = chunkResourceNamesByUrlLength_(resourceNames, baseApi, true, 1800)
    chunks.forEach(chunk=>{
        let query = chunk.map(name=>`resourceNames=${encodeURIComponent(name)}`).join("&")
        let api = `${baseApi}&${query}`
        let params = {
            method: "get",
            muteHttpExceptions: true,
        }
        let response = fetchWithService_(service, api, params)
        if (response.getResponseCode() < 200 || response.getResponseCode() >= 300){
            return
        }
        let data = JSON.parse(response.getContentText())
        let responses = data.responses || []
        responses.forEach(item=>{
            if (item.httpStatusCode && item.httpStatusCode !== 200){
                return
            }
            let person = item.person
            if (!person || !person.resourceName || !person.etag){
                return
            }
            out[person.resourceName] = person.etag
        })
    })
    return out
}

function getOwnerEtagsBatch_(resourceNames){
    let out = {}
    if (!resourceNames || !resourceNames.length){
        return out
    }
    let token = ScriptApp.getOAuthToken()
    let fields = encodeURIComponent("metadata")
    let baseApi = `https://people.googleapis.com/v1/people:batchGet?personFields=${fields}`
    let chunks = chunkResourceNamesByUrlLength_(resourceNames, baseApi, false, 1800)
    chunks.forEach(chunk=>{
        let query = chunk.map(name=>`resourceNames=${encodeURIComponent(name)}`).join("&")
        let api = `${baseApi}&${query}`
        let params = {
            headers: {
                Authorization: 'Bearer ' + token,
            },
            muteHttpExceptions: true,
        }
        let data = fetchJson_(api, params)
        let responses = data.responses || []
        responses.forEach(item=>{
            if (item.httpStatusCode && item.httpStatusCode !== 200){
                return
            }
            let person = item.person
            if (!person || !person.resourceName || !person.etag){
                return
            }
            out[person.resourceName] = person.etag
        })
    })
    return out
}

function stableStringify_(value){
    if (value === null || typeof value !== "object"){
        return JSON.stringify(value)
    }
    if (Array.isArray(value)){
        return "[" + value.map(v=>stableStringify_(v)).join(",") + "]"
    }
    let keys = Object.keys(value).sort()
    let parts = keys.map(key=>`${JSON.stringify(key)}:${stableStringify_(value[key])}`)
    return "{" + parts.join(",") + "}"
}

function computeHash_(value){
    let str = stableStringify_(value)
    let digest = Utilities.computeDigest(
        Utilities.DigestAlgorithm.SHA_256,
        str,
        Utilities.Charset.UTF_8
    )
    return Utilities.base64EncodeWebSafe(digest)
}

function computeContactHash_(memberData){
    let payload = sanitizeContactForCreate_(memberData)
    delete(payload.memberships)
    delete(payload.photos)
    return computeHash_(payload)
}

function computePhotoHash_(memberData){
    if (!memberData || !memberData.photos || !memberData.photos.length || !memberData.photos[0].url){
        return "no-photo"
    }
    return computeHash_(memberData.photos[0].url)
}

function buildFullUpdatePayload_(memberData){
    let clean = sanitizeContactForCreate_(memberData)
    let payload = {}
    SYNC_WRITABLE_FIELDS_.forEach(field=>{
        payload[field] = clean[field] || []
    })
    return payload
}

function updateMemberAs_(service, recipientPersonId, etag, memberData){
    if (!recipientPersonId || !etag){
        throw new Error("Missing recipient id or etag for update.")
    }
    let normalized = normalizePersonResourceName_(recipientPersonId)
    let api = `https://people.googleapis.com/v1/${normalized}:updateContact?updatePersonFields=${encodeURIComponent(SYNC_WRITABLE_FIELDS_.join(","))}`
    let payload = buildFullUpdatePayload_(memberData)
    payload.resourceName = normalized
    payload.etag = etag
    let params = {
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      method: "patch",
      muteHttpExceptions: true,
    }
    let response = fetchWithService_(service, api, params)
    let code = response.getResponseCode()
    if (code < 200 || code >= 300){
        throw new Error(`People API error (${code}) for ${api}: ${response.getContentText()}`)
    }
}

function deleteContactPhotoAs_(service, resourceName){
    if (!resourceName){
        return
    }
    let normalized = normalizePersonResourceName_(resourceName)
    let api = "https://people.googleapis.com/v1/" + normalized + ":deleteContactPhoto"
    let params = {
      method: "delete",
      muteHttpExceptions: true,
    }
    let resp = fetchWithService_(service, api, params)
    if (resp.getResponseCode() < 200 || resp.getResponseCode() >= 300){
        Logger.log("deleteContactPhoto as recipient failed: %s", resp.getContentText())
    }
}


function doGet(e){
    let template = HtmlService.createTemplateFromFile("index")
    let viewMode = (e && e.parameter && e.parameter.mode) ? e.parameter.mode : "owner"
    template.viewMode = viewMode
    
    let app = template.evaluate()
    app
        .setTitle("Share Google Contacts")
        .addMetaTag("viewport", "width=device-width, initial-scale=1.0")
        .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL)
    return app
}


function loadApp(viewMode){
    let profile = getProfile()
    let key = profile.email
    let groups = {}
    let sharedGroups = []
    if (viewMode === "shared"){
        let appData = getAppData(key) || {}
        if (appData.sharedGroups){
            sharedGroups = appData.sharedGroups
        }
    } else {
        let result = listContactGroups(key)
        groups = result.groups
        sharedGroups = result.sharedGroups
    }
    
    let createOptions = (viewMode === "shared") ? {maxOps: 0} : {}
    sharedGroups = createSharedGroups(sharedGroups, createOptions)
    let recipientConnected = isRecipientConnected_()
    
    let appData = {
        groups,
        sharedGroups,
        recipientConnected,
        profile,
    }
    
    // Only persist compact data to avoid property quota
    try {
        saveAppData(key, compactAppData_(appData))
    } catch (e) {
        if (String(e).toLowerCase().includes("property storage quota")) {
            cleanupPeopleProperties_()
            saveAppData(key, compactAppData_(appData))
        } else {
            throw e
        }
    }

    return appData
}

function processSharedImports(maxOps){
    let profile = getProfile()
    let key = profile.email
    let service = getRecipientService_(key)
    if (!service.hasAccess()){
        throw new Error("Connect your account first, then retry import.")
    }
    let lock = LockService.getUserLock()
    if (!lock.tryLock(5000)){
        let busyData = getAppData(key) || {}
        return {sharedGroups: busyData.sharedGroups || [], busy: true}
    }
    try {
        let appData = getAppData(key) || {}
        let sharedGroups = appData.sharedGroups || []
        let options = {}
        if (typeof maxOps === "number" && maxOps >= 0){
            options.maxOps = maxOps
        }
        options.timeBudgetMs = RECIPIENT_IMPORT_TIME_BUDGET_MS_
        options.recipientService = service
        sharedGroups = createSharedGroups(sharedGroups, options)
        appData.sharedGroups = sharedGroups
        saveAppData(key, appData)
        return {sharedGroups}
    } finally {
        lock.releaseLock()
    }
}

function processSharedImportForGroup(ownerKey, resourceName, maxOps){
    let profile = getProfile()
    let recipientKey = profile.email
    let service = getRecipientService_(recipientKey)
    if (!service.hasAccess()){
        throw new Error("Connect your account first, then retry import.")
    }
    let lock = LockService.getUserLock()
    if (!lock.tryLock(5000)){
        let busyData = getAppData(recipientKey) || {}
        return {sharedGroups: busyData.sharedGroups || [], busy: true}
    }
    try {
        let appData = getAppData(recipientKey) || {}
        let sharedGroups = appData.sharedGroups || []
        let targetIndex = sharedGroups.findIndex(group=>
            group &&
            group.owner === ownerKey &&
            group.resourceName === resourceName
        )
        if (targetIndex < 0){
            return {sharedGroups}
        }
        let targetGroup = sharedGroups[targetIndex]
        let options = {}
        if (typeof maxOps === "number" && maxOps >= 0){
            options.maxOps = maxOps
        }
        options.timeBudgetMs = RECIPIENT_IMPORT_TIME_BUDGET_MS_
        options.recipientService = service
        let processed = createSharedGroups([targetGroup], options)

        // Replace existing entry with processed result (or remove if stale filtered out).
        sharedGroups = sharedGroups.filter(group=>
            !(group && group.owner === ownerKey && group.resourceName === resourceName)
        )
        if (processed && processed.length){
            let insertAt = Math.min(targetIndex, sharedGroups.length)
            sharedGroups.splice(insertAt, 0, processed[0])
        }
        appData.sharedGroups = sharedGroups
        saveAppData(recipientKey, appData)
        return {sharedGroups}
    } finally {
        lock.releaseLock()
    }
}

function getProfile(){
    let api = "https://people.googleapis.com/v1/people/me?personFields=names,photos,emailAddresses"
    let token = ScriptApp.getOAuthToken()
    
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      muteHttpExceptions: true,
    }
    let {resourceName, names, photos, emailAddresses} = fetchJson_(api, params)
    let displayName = names[0].displayName
    let url = photos[0].url
    let email = emailAddresses[0].value
    
    let profile = {displayName, email, url, resourceName}
    return profile
}

function normalizeAppDataKey_(key){
    return String(key || "").trim().toLowerCase()
}



function getAppData(key){
    let normalizedKey = normalizeAppDataKey_(key)
    if (!normalizedKey){
        return null
    }
    let scriptProps = PropertiesService.getScriptProperties()
    let raw = scriptProps.getProperty(normalizedKey)
    if (!raw && key && key !== normalizedKey){
        // Backward-compatible read for pre-normalization keys.
        raw = scriptProps.getProperty(key)
        if (raw){
            scriptProps.setProperty(normalizedKey, raw)
            scriptProps.deleteProperty(key)
        }
    }
    if (!raw){
        return null
    }
    let appData
    try {
        appData = JSON.parse(raw)
    } catch (e) {
        return null
    }
    return appData
}


function saveAppData(key, appData){
    let normalizedKey = normalizeAppDataKey_(key)
    if (!normalizedKey){
        throw new Error("Missing app data key.")
    }
    let scriptProps = PropertiesService.getScriptProperties()    
    let value = JSON.stringify(appData)
    try {
        scriptProps.setProperty(normalizedKey, value)
    } catch (e) {
        if (String(e).toLowerCase().includes("property storage quota")) {
            scriptProps.deleteProperty(normalizedKey)
            scriptProps.setProperty(normalizedKey, value)
        } else {
            throw e
        }
    }
}

function compactAppData_(appData){
    let compactGroups = {}
    if (appData.groups){
        Object.keys(appData.groups).forEach(resourceName=>{
            let group = appData.groups[resourceName]
            compactGroups[resourceName] = {
                shared: group.shared || []
            }
        })
    }
    return {
        groups: compactGroups,
        sharedGroups: appData.sharedGroups || []
    }
}

function getOwnerSkippedCounts_(ownerKey, groups){
    let counts = {}
    if (!groups){
        return counts
    }
    Object.keys(groups).forEach(resourceName=>{
        counts[resourceName] = 0
        let group = groups[resourceName] || {}
        let recipients = group.shared || []
        recipients.forEach(recipientEmail=>{
            let recipientAppData = getAppData(recipientEmail) || {}
            let recipientSharedGroups = recipientAppData.sharedGroups || []
            let sharedGroup = recipientSharedGroups.find(item=>
                item &&
                item.owner === ownerKey &&
                item.resourceName === resourceName
            )
            if (!sharedGroup){
                return
            }
            let skippedIndexes = Array.isArray(sharedGroup.skippedIndexes) ? sharedGroup.skippedIndexes : []
            if (skippedIndexes.length){
                counts[resourceName] += skippedIndexes.length
                return
            }
            let skippedCount = Number(sharedGroup.skippedCount || 0)
            if (skippedCount > 0){
                counts[resourceName] += skippedCount
            }
        })
    })
    return counts
}

function getOwnerSyncProgressByGroup_(ownerKey, groups){
    let progressByGroup = {}
    if (!groups){
        return progressByGroup
    }
    Object.keys(groups).forEach(resourceName=>{
        let group = groups[resourceName] || {}
        let recipients = Array.isArray(group.shared) ? group.shared : []
        let total = 0
        let done = 0
        let active = false
        recipients.forEach(recipientEmail=>{
            let recipientAppData = getAppData(recipientEmail) || {}
            let recipientSharedGroups = recipientAppData.sharedGroups || []
            let sharedGroup = recipientSharedGroups.find(item=>
                item &&
                item.owner === ownerKey &&
                item.resourceName === resourceName
            )
            if (!sharedGroup || !sharedGroup.ownerSyncState){
                return
            }
            let state = sharedGroup.ownerSyncState
            let groupTotal = Number(state.total || group.memberCount || 0)
            let pendingTotal = Number(
                state.pendingTotal ||
                (Array.isArray(state.pendingOwnerIds) ? state.pendingOwnerIds.length : 0)
            )
            let pendingCursor = Number(state.pendingCursor || 0)
            let retryCount = Array.isArray(state.retryOwnerIds) ? state.retryOwnerIds.length : 0
            let phase = String(state.phase || "")
            let processedWithinPending = Math.min(Math.max(0, pendingCursor), Math.max(0, pendingTotal))
            let remainingInPending = Math.max(0, pendingTotal - processedWithinPending)
            let remaining = remainingInPending + Math.max(0, retryCount)
            let recipientDone = Math.max(0, groupTotal - remaining)
            if (recipientDone > groupTotal){
                recipientDone = groupTotal
            }
            total += Math.max(0, groupTotal)
            done += recipientDone
            if (phase !== "done" || pendingCursor < pendingTotal || retryCount > 0){
                active = true
            }
        })
        if (total > 0 || active){
            progressByGroup[resourceName] = {
                done: Math.max(0, done),
                total: Math.max(0, total),
                active,
                label: `${Math.max(0, done)}/${Math.max(0, total)}`
            }
        }
    })
    return progressByGroup
}

function getCachedPerson_(resourceName){
    let cache = CacheService.getScriptCache()
    let cached = cache.get(`person:${resourceName}`)
    if (cached){
        return JSON.parse(cached)
    }
}

function setCachedPerson_(resourceName, data){
    let cache = CacheService.getScriptCache()
    cache.put(`person:${resourceName}`, JSON.stringify(data), 21600)
}

function cleanupPeopleProperties_(){
    let scriptProps = PropertiesService.getScriptProperties()
    let keys = scriptProps.getKeys()
    keys.forEach(key=>{
        if (key.startsWith("people/")){
            scriptProps.deleteProperty(key)
        }
    })
}


function shareContactGroup(key,resourceName, name, members, memberCount, emails, notifyRecipients){
    
    let owner = key
    emails = Array.from(new Set((emails || []).map(email=>String(email || "").trim().toLowerCase()).filter(Boolean)))
    let shouldNotify = (notifyRecipients === undefined || notifyRecipients === null) ? true : !!notifyRecipients
    let allowedEmails = []
    let blockedNoInviteEmails = []
    emails.forEach(email=>{
        if (!shouldNotify && !hasPriorShareInvite_(owner, email)){
            blockedNoInviteEmails.push(email)
            return
        }
        allowedEmails.push(email)
    })
    // Always read members from People API to avoid stale/partial member lists from UI state.
    let details = getGroupDetails_(resourceName, 2000)
    members = details.memberResourceNames || []
    memberCount = members.length
    let appData = getAppData(key)
    let groups = appData.groups
    let group = groups[resourceName]
    let shared = Array.from(new Set((group.shared || []).concat(allowedEmails)))
    
    appData.groups[resourceName].shared = shared
    saveAppData(key, appData)

    // Store member data in shared sheet for recipients
    let requestedContacts = members.length
    let preparedContacts = 0
    let missingContacts = 0
    let membersData = []
    if (members && members.length){
        // Use resilient owner fetch path (chunked + bandwidth fallback) so large shares
        // do not stop after partial preparation.
        let membersDataById = fetchOwnerMemberDataByIds_(members)
        membersData = members
            .map(member=>membersDataById[member])
            .filter(Boolean)
        preparedContacts = membersData.length
        missingContacts = requestedContacts - preparedContacts
    }

    let trustedAccessibleByEmail = {}

    // share group to Collaborators
    allowedEmails.forEach(email=>{
        let shareId = Utilities.getUuid()
        if (membersData.length){
            writeSharedContacts_(shareId, owner, name, resourceName, membersData)
        }
        let appData = getAppData(email)
        
        if (!appData){
            appData = {}
        }
        
        
        let sharedGroups = appData.sharedGroups || []
        
        // Keep recipient appData light; full member payload is stored in SharedContacts sheet.
        let sharedGroup = {owner, name, members: [], memberCount, resourceName, shareId}
        let existingIndex = sharedGroups.findIndex(g => g.owner === owner && g.resourceName === resourceName)
        if (existingIndex >= 0){
            let existing = sharedGroups[existingIndex] || {}
            // If recipient already imported/created this shared group before, treat access as trusted.
            if (existing.created || Number(existing.importCursor || 0) > 0){
                trustedAccessibleByEmail[email] = true
            }
            sharedGroups[existingIndex] = Object.assign({}, existing, sharedGroup, {
                created: existing.created || "",
                importCursor: existing.importCursor || 0
            })
        } else {
            sharedGroups.push(sharedGroup)
        }
        
        appData.sharedGroups = sharedGroups
        saveAppData(email, appData)
    })

    let sheetAccess = ensureSharedSheetAccess_(allowedEmails, {
        trustedAccessible: trustedAccessibleByEmail
    })

    if (shouldNotify){
        sendSharedContactGroup(owner, name, allowedEmails)
    }
    
    return {
        groups: appData.groups,
        shareStatus: {
            sent: allowedEmails.length,
            notified: shouldNotify ? allowedEmails.length : 0,
            sheetAccessGranted: Number(sheetAccess.granted || 0),
            sheetAccessFailed: Number((sheetAccess.failedEmails || []).length || 0),
            sheetAccessFailedEmails: sheetAccess.failedEmails || [],
            blockedNoInvite: blockedNoInviteEmails.length,
            blockedNoInviteEmails,
            requiresOwnerSync: allowedEmails.length > 0,
            requestedContacts,
            preparedContacts,
            missingContacts
        }
    }
}

function hasPriorShareInvite_(owner, recipientEmail){
    let appData = getAppData(recipientEmail) || {}
    let invitedByOwner = appData.invitedByOwner || {}
    if (invitedByOwner[owner]){
        return true
    }
    let sharedGroups = appData.sharedGroups || []
    return sharedGroups.some(group=>group && group.owner === owner)
}

function ensureOwnerSyncState_(group, total, reset){
    if (!group.ownerSyncState || reset || Number(group.ownerSyncState.total || 0) !== Number(total || 0)){
        group.ownerSyncState = {
            phase: "init",
            total: Number(total || 0),
            cursor: 0,
            pendingOwnerIds: [],
            pendingCursor: 0,
            pendingTotal: 0,
            retryOwnerIds: [],
            retryAttempts: {},
            reconcileMisses: {},
            skippedOwnerIds: [],
            initialized: false,
            deletedCount: 0,
            createdCount: 0,
            updatedCount: 0,
            photoCount: 0,
            failedCount: 0,
            lastProgressDone: 0,
            lastProgressRetry: 0,
            progressStallCount: 0,
            updatedAt: new Date().toISOString(),
        }
    }
    return group.ownerSyncState
}

function fetchOwnerMemberDataByIds_(ownerIds){
    let uniqueIds = Array.from(new Set((ownerIds || []).filter(Boolean)))
    if (!uniqueIds.length){
        return {}
    }
    let out = {}
    for (let i = 0; i < uniqueIds.length; i += OWNER_FETCH_BATCH_SIZE_){
        let chunk = uniqueIds.slice(i, i + OWNER_FETCH_BATCH_SIZE_)
        try {
            let batch = getPeopleDataBatch_(chunk)
            Object.keys(batch).forEach(key=>{
                out[key] = batch[key]
            })
        } catch (e) {
            if (!isBandwidthQuotaError_(e)){
                throw e
            }
            chunk.forEach(ownerPersonId=>{
                try {
                    let memberData = getPeopleData(ownerPersonId, false)
                    if (memberData){
                        out[ownerPersonId] = memberData
                    }
                } catch (ignored) {}
            })
            Utilities.sleep(250)
        }
    }
    return out
}

function fetchOwnerEtagsByIds_(ownerIds){
    let uniqueIds = Array.from(new Set((ownerIds || []).filter(Boolean)))
    if (!uniqueIds.length){
        return {}
    }
    let out = {}
    for (let i = 0; i < uniqueIds.length; i += OWNER_FETCH_BATCH_SIZE_){
        let chunk = uniqueIds.slice(i, i + OWNER_FETCH_BATCH_SIZE_)
        try {
            let batch = getOwnerEtagsBatch_(chunk)
            Object.keys(batch).forEach(key=>{
                out[key] = batch[key]
            })
        } catch (e) {
            if (!isBandwidthQuotaError_(e)){
                throw e
            }
            Utilities.sleep(250)
        }
    }
    return out
}

function ownerSyncMarkFailure_(state, ownerPersonId){
    let key = String(ownerPersonId)
    let nextCount = Number(state.retryAttempts[key] || 0) + 1
    if (nextCount >= OWNER_SYNC_MAX_RETRIES_){
        delete state.retryAttempts[key]
        if (!state.skippedOwnerIds.includes(ownerPersonId)){
            state.skippedOwnerIds.push(ownerPersonId)
        }
    } else {
        state.retryAttempts[key] = nextCount
        state.retryOwnerIds.push(ownerPersonId)
    }
    state.failedCount = Number(state.failedCount || 0) + 1
}

function ownerSyncMarkSuccess_(state, ownerPersonId){
    delete state.retryAttempts[String(ownerPersonId)]
    if (state.reconcileMisses){
        delete state.reconcileMisses[String(ownerPersonId)]
    }
    state.skippedOwnerIds = state.skippedOwnerIds.filter(id=>id !== ownerPersonId)
}

function ownerSyncMarkReconcileMissing_(state, ownerPersonId){
    if (!state.reconcileMisses){
        state.reconcileMisses = {}
    }
    let key = String(ownerPersonId)
    let nextCount = Number(state.reconcileMisses[key] || 0) + 1
    if (nextCount >= OWNER_SYNC_MAX_RETRIES_){
        delete state.reconcileMisses[key]
        delete state.retryAttempts[key]
        state.retryOwnerIds = (state.retryOwnerIds || []).filter(id=>id !== ownerPersonId)
        if (!state.skippedOwnerIds.includes(ownerPersonId)){
            state.skippedOwnerIds.push(ownerPersonId)
        }
        state.failedCount = Number(state.failedCount || 0) + 1
        return false
    }
    state.reconcileMisses[key] = nextCount
    return true
}

function propagateOwnerContactToSiblingMappings_(service, mappingSheet, ownerKey, recipientEmail, item){
    if (!service || !mappingSheet || !item || !item.ownerPersonId || !item.memberData){
        return
    }
    let excludeRowNumber = item.mapping && item.mapping.rowNumber ? Number(item.mapping.rowNumber) : 0
    let siblings = getSiblingSyncMappingsForOwnerPerson_(
        mappingSheet,
        ownerKey,
        recipientEmail,
        item.ownerPersonId,
        excludeRowNumber
    )
    if (!siblings.length){
        return
    }
    let siblingRecipientIds = siblings
        .map(row=>normalizePersonResourceName_(row.recipientPersonId))
        .filter(Boolean)
    if (!siblingRecipientIds.length){
        return
    }
    let etagsById = getRecipientEtagsBatchAs_(service, siblingRecipientIds)
    let now = new Date().toISOString()
    let shouldTouchPhoto = (item.mode === "create") || item.photoChanged === true
    siblings.forEach(sibling=>{
        try {
            let normalized = normalizePersonResourceName_(sibling.recipientPersonId)
            let etag = etagsById[normalized]
            if (!etag){
                return
            }
            updateMemberAs_(service, sibling.recipientPersonId, etag, item.memberData)
            if (shouldTouchPhoto){
                if (item.photoHash === "no-photo"){
                    deleteContactPhotoAs_(service, sibling.recipientPersonId)
                } else {
                    setContactPhotoAs_(service, sibling.recipientPersonId, item.memberData)
                }
            }
            let values = [
                sibling.shareId,
                ownerKey,
                sibling.groupResourceName,
                recipientEmail,
                item.ownerPersonId,
                sibling.recipientPersonId,
                item.contactHash || sibling.lastHash || "",
                item.photoHash || sibling.lastPhotoHash || "",
                now,
                item.ownerEtag || sibling.ownerEtag || ""
            ]
            mappingSheet.getRange(sibling.rowNumber, 1, 1, values.length).setValues([values])
        } catch (e) {
            // Keep primary sync progressing even if sibling propagation fails.
        }
    })
}

function isRecentTimestamp_(value, maxAgeMs){
    if (!value){
        return false
    }
    let ts = new Date(value).getTime()
    if (!isFinite(ts)){
        return false
    }
    return (Date.now() - ts) <= maxAgeMs
}

function getOwnerPendingSyncGroups(ownerKey){
    let ownerAppData = getAppData(ownerKey) || {}
    let groups = ownerAppData.groups || {}
    let pending = []
    Object.keys(groups).forEach(resourceName=>{
        let group = groups[resourceName] || {}
        let shared = Array.isArray(group.shared) ? group.shared : []
        if (!shared.length){
            return
        }
        let isPending = false
        shared.forEach(recipientEmail=>{
            if (isPending){
                return
            }
            let recipientAppData = getAppData(recipientEmail) || {}
            let sharedGroups = recipientAppData.sharedGroups || []
            let recipientChanged = false
            let recipientGroup = sharedGroups.find(item=>
                item &&
                item.owner === ownerKey &&
                item.resourceName === resourceName
            )
            if (!recipientGroup || !recipientGroup.ownerSyncState){
                return
            }
            let state = recipientGroup.ownerSyncState
            let total = Number(state.total || 0)
            let cursor = Number(state.cursor || 0)
            let pendingTotal = Array.isArray(state.pendingOwnerIds) ? state.pendingOwnerIds.length : Number(state.pendingTotal || 0)
            let pendingCursor = Number(state.pendingCursor || 0)
            let retry = Array.isArray(state.retryOwnerIds) ? state.retryOwnerIds.length : 0
            let phase = String(state.phase || "")
            let stillPendingByPlan = pendingCursor < pendingTotal || retry > 0
            let looksPending = (phase !== "done" || cursor < total || stillPendingByPlan)
            if (looksPending){
                let isRecent = isRecentTimestamp_(state.updatedAt, OWNER_SYNC_AUTO_RESUME_MAX_AGE_MS_)
                if (isRecent){
                    isPending = true
                } else {
                    state.phase = "stalled"
                    state.updatedAt = new Date().toISOString()
                    recipientGroup.status = "Sync paused (stale). Tap Sync to resume."
                    recipientChanged = true
                }
            }
            if (recipientChanged){
                saveAppData(recipientEmail, recipientAppData)
            }
        })
        if (isPending){
            pending.push(resourceName)
        }
    })
    return pending
}

function getOwnerGroupSyncProgress(ownerKey, resourceName){
    let profile = getProfile()
    if (!profile || !profile.email || String(profile.email).toLowerCase() !== String(ownerKey || "").toLowerCase()){
        throw new Error("Unauthorized progress request.")
    }
    let ownerAppData = getAppData(ownerKey) || {}
    let groups = ownerAppData.groups || {}
    let group = groups[resourceName] || null
    let progressByGroup = getOwnerSyncProgressByGroup_(ownerKey, groups)
    return {
        group,
        progress: progressByGroup[resourceName] || null
    }
}

function clearOwnerSyncStateFields_(group){
    if (!group){
        return
    }
    delete group.ownerSyncState
    delete group.ownerSyncCursor
    delete group.ownerSyncRetryOwnerIds
    delete group.ownerSyncRetryAttempts
    delete group.ownerSyncSkippedOwnerIds
    delete group.ownerSyncDeletedCount
    delete group.ownerSyncCreatedCount
    delete group.ownerSyncUpdatedCount
    delete group.ownerSyncPhotoCount
    delete group.ownerSyncFailedCount
    delete group.ownerSyncInitialized
    delete group.ownerSyncTotal
}

function resetGroupSyncState(ownerKey, resourceName){
    let profile = getProfile()
    if (!profile || !profile.email || String(profile.email).toLowerCase() !== String(ownerKey || "").toLowerCase()){
        throw new Error("Unauthorized reset request.")
    }
    let ownerAppData = getAppData(ownerKey) || {}
    let ownerGroups = ownerAppData.groups || {}
    let ownerGroup = ownerGroups[resourceName]
    if (!ownerGroup){
        throw new Error("Group not found.")
    }
    let recipients = Array.isArray(ownerGroup.shared) ? ownerGroup.shared : []
    recipients.forEach(recipientEmail=>{
        let recipientAppData = getAppData(recipientEmail) || {}
        let sharedGroups = recipientAppData.sharedGroups || []
        let changed = false
        sharedGroups.forEach(sharedGroup=>{
            if (!sharedGroup){
                return
            }
            if (sharedGroup.owner === ownerKey && sharedGroup.resourceName === resourceName){
                clearOwnerSyncStateFields_(sharedGroup)
                if (String(sharedGroup.status || "").startsWith("Sync")){
                    sharedGroup.status = "Shared"
                } else if (String(sharedGroup.status || "").startsWith("Sync paused")){
                    sharedGroup.status = "Shared"
                }
                changed = true
            }
        })
        if (changed){
            recipientAppData.sharedGroups = sharedGroups
            saveAppData(recipientEmail, recipientAppData)
        }
    })

    delete ownerGroup.ownerSyncRunId
    delete ownerGroup.ownerSyncStartedAt
    ownerGroup.syncProgress = null
    ownerAppData.groups[resourceName] = ownerGroup
    saveAppData(ownerKey, ownerAppData)

    let result = listContactGroups(ownerKey)
    return {groups: result.groups, sharedGroups: result.sharedGroups}
}

function syncSharedGroup(ownerKey, resourceName, maxOps, reset){
    let lock = LockService.getUserLock()
    if (!lock.tryLock(5000)){
        let ownerBusyData = getAppData(ownerKey) || {}
        let busyGroups = ownerBusyData.groups || {}
        return {
            groups: busyGroups,
            syncStatus: {"system":"Waiting for prior sync call..."},
            progressByGroup: getOwnerSyncProgressByGroup_(ownerKey, busyGroups),
            pending: true,
            busy: true
        }
    }
    try {
        let startedAt = Date.now()
        let ownerAppData = getAppData(ownerKey)
        if (!ownerAppData || !ownerAppData.groups || !ownerAppData.groups[resourceName]){
            throw new Error("Group not found for owner.")
        }
        let ownerGroup = ownerAppData.groups[resourceName]
        let details = getGroupDetails_(resourceName, 2000)
        let memberResourceNames = details.memberResourceNames || []
        let groupName = details.name || ownerGroup.name || "Shared Group"
        if (!memberResourceNames.length){
            throw new Error("No members found to sync.")
        }
        let sharedEmails = ownerGroup.shared || []
        if (!sharedEmails.length){
            throw new Error("This group is not shared yet. Share it first, then sync.")
        }

        if (reset){
            ownerGroup.ownerSyncRunId = Utilities.getUuid()
            ownerGroup.ownerSyncStartedAt = new Date().toISOString()
        }

        let ownerSet = {}
        memberResourceNames.forEach(ownerPersonId=>{
            ownerSet[ownerPersonId] = true
        })

        let ownerMemberDataCache = {}
        let syncStatus = {}
        let pending = false
        let remainingOps = Math.max(1, Number(maxOps || OWNER_SYNC_BATCH_SIZE_))

        for (let i = 0; i < sharedEmails.length; i++){
            let email = sharedEmails[i]
            if (remainingOps <= 0 || (Date.now() - startedAt) > OWNER_SYNC_TIME_BUDGET_MS_){
                pending = true
                syncStatus[email] = "Queued"
                continue
            }

            let appData = getAppData(email)
            if (!appData || !appData.sharedGroups){
                syncStatus[email] = "No sharedGroups data"
                continue
            }
            let group = appData.sharedGroups.find(g => g.resourceName === resourceName && g.owner === ownerKey)
            if (!group){
                syncStatus[email] = "Group not found in recipient data"
                continue
            }
            // Keep recipient metadata in sync with owner's current group size/name so
            // recipient table counters don't drift (e.g. showing 12/11).
            group.memberCount = memberResourceNames.length
            if (groupName){
                group.name = groupName
            }
            let service = getRecipientService_(email)
            if (!service.hasAccess()){
                group.status = "Connect required"
                syncStatus[email] = "Connect required"
                saveAppData(email, appData)
                continue
            }

            let state = ensureOwnerSyncState_(group, memberResourceNames.length, !!reset)
            try {
                let targetGroupName = groupName.endsWith("(shared)") ? groupName : `${groupName} (shared)`
                let groupId = group.created
                if (!groupId){
                    groupId = findGroupByNameAs_(service, targetGroupName)
                }
                if (!groupId){
                    groupId = createNewGroupAs_(service, groupName)
                }
                if (!groupId){
                    syncStatus[email] = "Failed to create group"
                    continue
                }
                group.created = groupId

                let {
                    sheet: mappingSheet,
                    byOwnerId: mappingByOwnerId,
                    byOwnerIdAny
                } = loadSyncMappings_(group.shareId, ownerKey, resourceName, email)

                if (!state.initialized || state.phase === "init"){
                    let hasExistingMappings = Object.keys(mappingByOwnerId).length > 0
                    if (!hasExistingMappings){
                        deleteContactsInGroupAs_(service, groupId)
                    }
                    let removedMappingRows = []
                    Object.keys(mappingByOwnerId).forEach(ownerPersonId=>{
                        if (ownerSet[ownerPersonId]){
                            return
                        }
                        try {
                            let mapped = mappingByOwnerId[ownerPersonId]
                            if (mapped.recipientPersonId){
                                let usedElsewhere = hasOtherSyncMappingForOwnerPerson_(
                                    ownerKey,
                                    email,
                                    ownerPersonId,
                                    group.shareId,
                                    resourceName
                                )
                                if (usedElsewhere){
                                    removeContactFromGroupAs_(service, groupId, mapped.recipientPersonId)
                                } else {
                                    deleteContactAs_(service, mapped.recipientPersonId)
                                }
                            }
                            removedMappingRows.push(mapped.rowNumber)
                            state.deletedCount = Number(state.deletedCount || 0) + 1
                        } catch (e) {}
                    })
                    if (removedMappingRows.length){
                        deleteSyncMappingRows_(mappingSheet, removedMappingRows)
                        let refreshed = loadSyncMappings_(group.shareId, ownerKey, resourceName, email)
                        mappingSheet = refreshed.sheet
                        mappingByOwnerId = refreshed.byOwnerId
                        byOwnerIdAny = refreshed.byOwnerIdAny || {}
                    }

                    // Reuse existing recipient contact mappings from other groups for the same
                    // owner contact so updates propagate across groups.
                    let inheritedRows = []
                    let inheritedNow = new Date().toISOString()
                    memberResourceNames.forEach(ownerPersonId=>{
                        if (mappingByOwnerId[ownerPersonId]){
                            return
                        }
                        let anyMapping = byOwnerIdAny[ownerPersonId]
                        if (!anyMapping || !anyMapping.recipientPersonId){
                            return
                        }
                        try {
                            ensureContactInGroupAs_(service, groupId, anyMapping.recipientPersonId)
                        } catch (e) {
                            // Membership will be retried by reconciliation path if needed.
                        }
                        inheritedRows.push([
                            group.shareId,
                            ownerKey,
                            resourceName,
                            email,
                            ownerPersonId,
                            anyMapping.recipientPersonId,
                            anyMapping.lastHash || "",
                            anyMapping.lastPhotoHash || "",
                            inheritedNow,
                            anyMapping.ownerEtag || ""
                        ])
                    })
                    if (inheritedRows.length){
                        mappingSheet.getRange(mappingSheet.getLastRow() + 1, 1, inheritedRows.length, inheritedRows[0].length).setValues(inheritedRows)
                        let refreshed = loadSyncMappings_(group.shareId, ownerKey, resourceName, email)
                        mappingSheet = refreshed.sheet
                        mappingByOwnerId = refreshed.byOwnerId
                        byOwnerIdAny = refreshed.byOwnerIdAny || {}
                    }
                    let ownerEtagsForAll = fetchOwnerEtagsByIds_(memberResourceNames)
                    let candidatePendingOwnerIds = []
                    let unchangedMappedPairs = []
                    memberResourceNames.forEach(ownerPersonId=>{
                        let mapping = mappingByOwnerId[ownerPersonId]
                        let ownerEtag = ownerEtagsForAll[ownerPersonId] || ""
                        if (!mapping || !mapping.recipientPersonId){
                            candidatePendingOwnerIds.push(ownerPersonId)
                            return
                        }
                        if (!ownerEtag || mapping.ownerEtag !== ownerEtag){
                            candidatePendingOwnerIds.push(ownerPersonId)
                            return
                        }
                        unchangedMappedPairs.push({
                            ownerPersonId,
                            recipientPersonId: mapping.recipientPersonId
                        })
                    })
                    if (unchangedMappedPairs.length){
                        let recipientIds = unchangedMappedPairs
                            .map(item=>normalizePersonResourceName_(item.recipientPersonId))
                            .filter(Boolean)
                        let recipientEtags = getRecipientEtagsBatchAs_(service, recipientIds)
                        unchangedMappedPairs.forEach(item=>{
                            let key = normalizePersonResourceName_(item.recipientPersonId)
                            if (!recipientEtags[key]){
                                candidatePendingOwnerIds.push(item.ownerPersonId)
                            }
                        })
                    }
                    state.pendingOwnerIds = Array.from(new Set(candidatePendingOwnerIds))
                    state.pendingCursor = 0
                    state.pendingTotal = state.pendingOwnerIds.length
                    state.cursor = memberResourceNames.length
                    state.initialized = true
                    state.phase = "upsert"
                }

                let opsForRecipient = Math.min(remainingOps, OWNER_SYNC_BATCH_SIZE_)
                let candidateOwnerIds = []
                while (state.retryOwnerIds.length && candidateOwnerIds.length < opsForRecipient){
                    let nextRetryId = state.retryOwnerIds.shift()
                    if (!state.skippedOwnerIds.includes(nextRetryId)){
                        candidateOwnerIds.push(nextRetryId)
                    }
                }
                while (state.pendingCursor < state.pendingOwnerIds.length && candidateOwnerIds.length < opsForRecipient){
                    let nextPendingId = state.pendingOwnerIds[state.pendingCursor]
                    if (!state.skippedOwnerIds.includes(nextPendingId)){
                        candidateOwnerIds.push(nextPendingId)
                    }
                    state.pendingCursor++
                }

                let ownerEtagsById = {}
                try {
                    ownerEtagsById = getOwnerEtagsBatch_(candidateOwnerIds)
                } catch (e) {
                    if (!isBandwidthQuotaError_(e)){
                        throw e
                    }
                }

                let candidateMappedRecipientIds = candidateOwnerIds
                    .map(ownerPersonId=>{
                        let mapping = mappingByOwnerId[ownerPersonId]
                        return mapping && mapping.recipientPersonId
                            ? normalizePersonResourceName_(mapping.recipientPersonId)
                            : ""
                    })
                    .filter(Boolean)
                let recipientEtagsById = getRecipientEtagsBatchAs_(service, candidateMappedRecipientIds)

                let ownerIdsNeedingFullData = []
                let ownerIdsNeedingMembership = []
                candidateOwnerIds.forEach(ownerPersonId=>{
                    let mapping = mappingByOwnerId[ownerPersonId]
                    let ownerEtag = ownerEtagsById[ownerPersonId] || ""
                    if (!ownerEtag){
                        // Metadata fetch miss: continue with full-data path for resilience.
                        ownerIdsNeedingFullData.push(ownerPersonId)
                        return
                    }
                    if (mapping && mapping.recipientPersonId && mapping.ownerEtag && mapping.ownerEtag === ownerEtag){
                        let recipientKey = normalizePersonResourceName_(mapping.recipientPersonId)
                        if (!recipientEtagsById[recipientKey]){
                            // Mapping exists but recipient contact no longer exists; force recreate path.
                            ownerIdsNeedingFullData.push(ownerPersonId)
                            return
                        }
                        if (state.reconcileMisses && Number(state.reconcileMisses[String(ownerPersonId)] || 0) > 0){
                            // Contact exists but may have fallen out of the target group; ensure membership.
                            ownerIdsNeedingMembership.push(ownerPersonId)
                            return
                        }
                        ownerSyncMarkSuccess_(state, ownerPersonId)
                        return
                    }
                    ownerIdsNeedingFullData.push(ownerPersonId)
                })

                let idsToFetch = ownerIdsNeedingFullData.filter(ownerPersonId=>!ownerMemberDataCache[ownerPersonId])
                if (idsToFetch.length){
                    let fetched = fetchOwnerMemberDataByIds_(idsToFetch)
                    Object.keys(fetched).forEach(ownerPersonId=>{
                        ownerMemberDataCache[ownerPersonId] = fetched[ownerPersonId]
                    })
                }

                let updatesNeeded = []
                ownerIdsNeedingMembership.forEach(ownerPersonId=>{
                    let mapping = mappingByOwnerId[ownerPersonId]
                    if (!mapping || !mapping.recipientPersonId){
                        ownerIdsNeedingFullData.push(ownerPersonId)
                        return
                    }
                    updatesNeeded.push({
                        mode: "ensureMember",
                        ownerPersonId,
                        mapping,
                        contactHash: mapping.lastHash || "",
                        photoHash: mapping.lastPhotoHash || "",
                        ownerEtag: ownerEtagsById[ownerPersonId] || mapping.ownerEtag || ""
                    })
                })
                ownerIdsNeedingFullData.forEach(ownerPersonId=>{
                    let memberData = ownerMemberDataCache[ownerPersonId]
                    if (!memberData){
                        ownerSyncMarkFailure_(state, ownerPersonId)
                        return
                    }
                    let contactHash = computeContactHash_(memberData)
                    let photoHash = computePhotoHash_(memberData)
                    let mapping = mappingByOwnerId[ownerPersonId]
                    if (!mapping || !mapping.recipientPersonId){
                        updatesNeeded.push({
                            mode: "create",
                            ownerPersonId,
                            memberData,
                            contactHash,
                            photoHash,
                            mapping,
                            ownerEtag: ownerEtagsById[ownerPersonId] || ""
                        })
                        return
                    }
                    let contactChanged = mapping.lastHash !== contactHash
                    let photoChanged = mapping.lastPhotoHash !== photoHash
                    if (!contactChanged && !photoChanged){
                        if (mapping.ownerEtag !== (ownerEtagsById[ownerPersonId] || "")){
                            updatesNeeded.push({
                                mode: "etagOnly",
                                ownerPersonId,
                                contactHash,
                                photoHash,
                                mapping,
                                ownerEtag: ownerEtagsById[ownerPersonId] || ""
                            })
                            return
                        }
                        ownerSyncMarkSuccess_(state, ownerPersonId)
                        return
                    }
                    updatesNeeded.push({
                        mode: "update",
                        ownerPersonId,
                        memberData,
                        contactHash,
                        photoHash,
                            mapping,
                            ownerEtag: ownerEtagsById[ownerPersonId] || "",
                            contactChanged,
                            photoChanged
                        })
                })

                let existingIdsToUpdate = updatesNeeded
                    .filter(item=>item.mode === "update")
                    .map(item=>normalizePersonResourceName_(item.mapping.recipientPersonId))
                let etagsByRecipientId = getRecipientEtagsBatchAs_(service, existingIdsToUpdate)
                let newRows = []
                let updateRows = []

                updatesNeeded.forEach(item=>{
                    let now = new Date().toISOString()
                    try {
                        if (item.mode === "ensureMember"){
                            ensureContactInGroupAs_(service, groupId, item.mapping.recipientPersonId)
                            ownerSyncMarkSuccess_(state, item.ownerPersonId)
                            updateRows.push({
                                rowNumber: item.mapping.rowNumber,
                                values: [
                                    group.shareId,
                                    ownerKey,
                                    resourceName,
                                    email,
                                    item.ownerPersonId,
                                    item.mapping.recipientPersonId,
                                    item.mapping.lastHash || item.contactHash || "",
                                    item.mapping.lastPhotoHash || item.photoHash || "",
                                    now,
                                    item.ownerEtag || ""
                                ]
                            })
                            return
                        }
                        if (item.mode === "etagOnly"){
                            ownerSyncMarkSuccess_(state, item.ownerPersonId)
                            updateRows.push({
                                rowNumber: item.mapping.rowNumber,
                                values: [
                                    group.shareId,
                                    ownerKey,
                                    resourceName,
                                    email,
                                    item.ownerPersonId,
                                    item.mapping.recipientPersonId,
                                    item.mapping.lastHash || item.contactHash || "",
                                    item.mapping.lastPhotoHash || item.photoHash || "",
                                    now,
                                    item.ownerEtag || ""
                                ]
                            })
                            return
                        }
                        if (item.mode === "create"){
                            let createdId = createNewMemberAs_(service, item.memberData, groupId)
                            if (!createdId){
                                ownerSyncMarkFailure_(state, item.ownerPersonId)
                                return
                            }
                            ownerSyncMarkSuccess_(state, item.ownerPersonId)
                            state.createdCount = Number(state.createdCount || 0) + 1
                            newRows.push([
                                group.shareId,
                                ownerKey,
                                resourceName,
                                email,
                                item.ownerPersonId,
                                createdId,
                                item.contactHash,
                                item.photoHash,
                                now,
                                item.ownerEtag || ""
                            ])
                            mappingByOwnerId[item.ownerPersonId] = {
                                ownerPersonId: item.ownerPersonId,
                                recipientPersonId: createdId,
                                lastHash: item.contactHash,
                                lastPhotoHash: item.photoHash,
                                ownerEtag: item.ownerEtag || "",
                            }
                            propagateOwnerContactToSiblingMappings_(service, mappingSheet, ownerKey, email, item)
                            return
                        }

                        let normalized = normalizePersonResourceName_(item.mapping.recipientPersonId)
                        let etag = etagsByRecipientId[normalized]
                        let recipientPersonId = item.mapping.recipientPersonId
                        if (!etag){
                            let recreatedId = createNewMemberAs_(service, item.memberData, groupId)
                            if (!recreatedId){
                                ownerSyncMarkFailure_(state, item.ownerPersonId)
                                return
                            }
                            recipientPersonId = recreatedId
                            state.createdCount = Number(state.createdCount || 0) + 1
                        } else {
                            if (item.contactChanged){
                                updateMemberAs_(service, item.mapping.recipientPersonId, etag, item.memberData)
                                state.updatedCount = Number(state.updatedCount || 0) + 1
                            }
                            if (item.photoChanged){
                                if (item.photoHash === "no-photo"){
                                    deleteContactPhotoAs_(service, item.mapping.recipientPersonId)
                                } else {
                                    setContactPhotoAs_(service, item.mapping.recipientPersonId, item.memberData)
                                }
                                state.photoCount = Number(state.photoCount || 0) + 1
                            }
                        }

                        ownerSyncMarkSuccess_(state, item.ownerPersonId)
                        updateRows.push({
                            rowNumber: item.mapping.rowNumber,
                            values: [
                                group.shareId,
                                ownerKey,
                                resourceName,
                                email,
                                item.ownerPersonId,
                                recipientPersonId,
                                item.contactHash,
                                item.photoHash,
                                now,
                                item.ownerEtag || ""
                            ]
                        })
                        propagateOwnerContactToSiblingMappings_(service, mappingSheet, ownerKey, email, item)
                    } catch (e) {
                        ownerSyncMarkFailure_(state, item.ownerPersonId)
                    }
                })

                if (newRows.length){
                    mappingSheet.getRange(mappingSheet.getLastRow() + 1, 1, newRows.length, newRows[0].length).setValues(newRows)
                }
                updateRows.forEach(row=>{
                    mappingSheet.getRange(row.rowNumber, 1, 1, row.values.length).setValues([row.values])
                })

                state.retryOwnerIds = Array.from(new Set(state.retryOwnerIds))
                state.skippedOwnerIds = Array.from(new Set(state.skippedOwnerIds))
                state.updatedAt = new Date().toISOString()

                // Final consistency pass: if sync appears complete, verify recipient still
                // has every mapped owner contact and re-queue any missing ones.
                if (state.pendingCursor >= state.pendingOwnerIds.length && state.retryOwnerIds.length === 0){
                    try {
                        let recipientDetails = getGroupDetailsAs_(service, groupId, 2000)
                        let recipientMembers = recipientDetails.memberResourceNames || []
                        let recipientSet = {}
                        recipientMembers.forEach(id=>{
                            recipientSet[normalizePersonResourceName_(id)] = true
                        })
                        let missingOwnerIds = []
                        memberResourceNames.forEach(ownerPersonId=>{
                            let mapping = mappingByOwnerId[ownerPersonId]
                            if (!mapping || !mapping.recipientPersonId){
                                missingOwnerIds.push(ownerPersonId)
                                return
                            }
                            let normalizedRecipientId = normalizePersonResourceName_(mapping.recipientPersonId)
                            if (!recipientSet[normalizedRecipientId]){
                                missingOwnerIds.push(ownerPersonId)
                            }
                        })
                        if (missingOwnerIds.length){
                            missingOwnerIds.forEach(ownerPersonId=>{
                                if (state.skippedOwnerIds.includes(ownerPersonId)){
                                    return
                                }
                                let shouldRetry = ownerSyncMarkReconcileMissing_(state, ownerPersonId)
                                if (shouldRetry){
                                    state.retryOwnerIds.push(ownerPersonId)
                                }
                            })
                            state.retryOwnerIds = Array.from(new Set(state.retryOwnerIds))
                        }
                    } catch (e) {
                        // Ignore reconciliation read errors and continue with current state.
                    }
                }

                state.skippedOwnerIds = Array.from(new Set(state.skippedOwnerIds))
                group.skippedCount = state.skippedOwnerIds.length

                let done = state.pendingCursor >= state.pendingOwnerIds.length && state.retryOwnerIds.length === 0
                if (done){
                    state.phase = "done"
                    let skipped = Number(group.skippedCount || 0)
                    group.status = skipped ? `Shared (${skipped} skipped)` : "Shared"
                    let summary = `${Number(state.createdCount || 0)} created, ${Number(state.updatedCount || 0)} updated, ${Number(state.photoCount || 0)} photos, ${Number(state.deletedCount || 0)} deleted`
                    syncStatus[email] = skipped
                        ? `Synced (${summary}, ${skipped} skipped)`
                        : `Synced (${summary})`
                } else {
                    let retryLabel = state.retryOwnerIds.length ? `, retry ${state.retryOwnerIds.length}` : ""
                    let pendingTotal = Number(
                        state.pendingTotal ||
                        (Array.isArray(state.pendingOwnerIds) ? state.pendingOwnerIds.length : 0)
                    )
                    let pendingCursor = Number(state.pendingCursor || 0)
                    let processedWithinPending = Math.min(Math.max(0, pendingCursor), Math.max(0, pendingTotal))
                    let remainingInPending = Math.max(0, pendingTotal - processedWithinPending)
                    let remaining = remainingInPending + Math.max(0, state.retryOwnerIds.length)
                    let totalForStatus = Number(state.total || memberResourceNames.length || pendingTotal || 0)
                    let doneForStatus = Math.max(0, totalForStatus - remaining)
                    if (doneForStatus > totalForStatus){
                        doneForStatus = totalForStatus
                    }

                    // If progress remains flat for many cycles, promote remaining retries
                    // to skipped so a single stubborn contact cannot stall the whole run.
                    let lastDone = Number(state.lastProgressDone || 0)
                    let lastRetry = Number(state.lastProgressRetry || 0)
                    if (doneForStatus === lastDone && state.retryOwnerIds.length === lastRetry && remaining > 0){
                        state.progressStallCount = Number(state.progressStallCount || 0) + 1
                    } else {
                        state.progressStallCount = 0
                    }
                    state.lastProgressDone = doneForStatus
                    state.lastProgressRetry = state.retryOwnerIds.length
                    if (state.progressStallCount >= 12 && state.retryOwnerIds.length > 0){
                        let stuckIds = Array.from(new Set(state.retryOwnerIds))
                        state.retryOwnerIds = []
                        stuckIds.forEach(ownerPersonId=>{
                            let key = String(ownerPersonId)
                            delete state.retryAttempts[key]
                            if (state.reconcileMisses){
                                delete state.reconcileMisses[key]
                            }
                            if (!state.skippedOwnerIds.includes(ownerPersonId)){
                                state.skippedOwnerIds.push(ownerPersonId)
                            }
                        })
                        state.failedCount = Number(state.failedCount || 0) + stuckIds.length
                    }

                    // Recompute with any forced skip adjustments above.
                    done = state.pendingCursor >= state.pendingOwnerIds.length && state.retryOwnerIds.length === 0
                    if (done){
                        state.phase = "done"
                        let skipped = Number(state.skippedOwnerIds.length || 0)
                        group.skippedCount = skipped
                        group.status = skipped ? `Shared (${skipped} skipped)` : "Shared"
                        let summary = `${Number(state.createdCount || 0)} created, ${Number(state.updatedCount || 0)} updated, ${Number(state.photoCount || 0)} photos, ${Number(state.deletedCount || 0)} deleted`
                        syncStatus[email] = skipped
                            ? `Synced (${summary}, ${skipped} skipped)`
                            : `Synced (${summary})`
                    } else {
                    group.status = `Syncing (${doneForStatus}/${totalForStatus}${retryLabel})`
                    syncStatus[email] = group.status
                    pending = true
                    }
                }

                remainingOps -= Math.max(1, candidateOwnerIds.length || updatesNeeded.length)
                saveAppData(email, appData)
            } catch (e) {
                group.status = "Error: " + e.message
                syncStatus[email] = group.status
                saveAppData(email, appData)
            }
        }

        if (!pending){
            delete ownerGroup.ownerSyncRunId
            delete ownerGroup.ownerSyncStartedAt
        }
        let latestProgress = getOwnerSyncProgressByGroup_(ownerKey, ownerAppData.groups || {})
        Object.keys(ownerAppData.groups || {}).forEach(groupId=>{
            let progress = latestProgress[groupId] || null
            if (progress && progress.active){
                ownerAppData.groups[groupId].syncProgress = progress
            } else {
                ownerAppData.groups[groupId].syncProgress = null
            }
        })
        saveAppData(ownerKey, ownerAppData)
        return {
            groups: ownerAppData.groups,
            syncStatus,
            progressByGroup: latestProgress,
            pending
        }
    } finally {
        lock.releaseLock()
    }
}

function sendSharedContactGroup(owner, name, emails){
    let url = getPageUrl()
    url += (url.indexOf("?") >= 0 ? "&" : "?") + "mode=shared"
    let subject = "Share Google Contacts"
    let htmlBody = `<p>Hi there,<\/p>
        <p>${owner} is sharing the contact group <strong>${name}<\/strong> with you, please visit the <a href="${url}">link<\/a> and sync the contact group to your Google contacts.<\/p>
        <p>Share Google Contacts<\/p>`
    let recipients = Array.from(new Set((emails || []).map(email=>String(email || "").trim()).filter(Boolean)))
    recipients.forEach(email=>{
        let options = {htmlBody}
        GmailApp.sendEmail(email, subject, "Open this link to sync shared contacts: " + url, options)
        try {
            let appData = getAppData(email) || {}
            let invitedByOwner = appData.invitedByOwner || {}
            invitedByOwner[owner] = new Date().toISOString()
            appData.invitedByOwner = invitedByOwner
            saveAppData(email, appData)
        } catch (e) {
            // Best effort only.
        }
    })
}

function stopShareContactGroup(key, email, resourceName){
    
    // remove the group from the shared group list
    let appData = getAppData(email)
    let sharedGroups = []
    if (appData.sharedGroups){
        sharedGroups = appData.sharedGroups
        sharedGroups = sharedGroups.filter(group => group.resourceName !== resourceName)
    }
    
    appData.sharedGroups = sharedGroups
    saveAppData(email, appData)
    
    // remove the email from the shared list for the current user
    appData = getAppData(key)
    let groups = appData.groups
    let group = groups[resourceName]
    let shared = group.shared.filter(sharedUserEmail => sharedUserEmail !== email)
    appData.groups[resourceName].shared = shared
    saveAppData(key, appData)
    
    return appData
}


function listContactGroups(key){
    let appData = getAppData(key)
    
    let groups = {}
    let sharedByGroupId = {}
    let sharedGroups = []
    
    if (appData){
        if(appData.groups){
            Object.keys(appData.groups).forEach(resourceName=>{
                let cached = appData.groups[resourceName] || {}
                sharedByGroupId[resourceName] = cached.shared || []
            })
        }
        if(appData.sharedGroups){
            sharedGroups = appData.sharedGroups
        }
    }
    
    let baseApi = "https://people.googleapis.com/v1/contactGroups?pageSize=30"
    
    let token = ScriptApp.getOAuthToken()
    
    
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      muteHttpExceptions: true,
    }
    
    let nextPageToken
    do {
        let api = nextPageToken ? `${baseApi}&pageToken=${nextPageToken}` : baseApi
        let data = fetchJson_(api, params)
        if (!data.contactGroups){
            throw new Error(`People API error: contactGroups missing for ${api}`)
        }
        data.contactGroups.forEach(({resourceName, groupType, name, memberCount})=>{
            let groupName = (typeof name === "string") ? name : ""
            // Hide only app-created recipient clones that use the exact suffix " (shared)".
            let isAppSharedClone = groupName.endsWith(" (shared)")
            if (!isAppSharedClone && groupType === "USER_CONTACT_GROUP"){
                let shared = sharedByGroupId[resourceName] || []
                
                let members = []
                
                groups[resourceName] = {resourceName, groupType, name: groupName, memberCount, members, shared}
            }
        })
        nextPageToken = data.nextPageToken
    }while (nextPageToken)

    // Sort groups alphabetically by name before returning
    let skippedCounts = getOwnerSkippedCounts_(key, groups)
    let syncProgressByGroup = getOwnerSyncProgressByGroup_(key, groups)

    let sortedGroups = {}
    Object.keys(groups)
        .sort((a, b) => {
            let nameA = (groups[a].name || "").toLowerCase()
            let nameB = (groups[b].name || "").toLowerCase()
            return nameA.localeCompare(nameB)
        })
        .forEach(key => {
            groups[key].skippedCount = skippedCounts[key] || 0
            groups[key].syncProgress = syncProgressByGroup[key] || null
            sortedGroups[key] = groups[key]
        })

    return {groups: sortedGroups, sharedGroups}
}

function normalizeContactSignatureText_(value){
    return String(value || "")
        .trim()
        .toLowerCase()
        .replace(/\s+/g, " ")
}

function normalizeContactSignaturePhone_(value){
    let digits = String(value || "").replace(/[^\d]/g, "")
    if (digits.length === 11 && digits.charAt(0) === "1"){
        digits = digits.slice(1)
    }
    return digits
}

function buildContactSignatures_(memberData){
    let signatures = {}
    if (!memberData){
        return []
    }

    let emails = Array.isArray(memberData.emailAddresses) ? memberData.emailAddresses : []
    emails.forEach(item=>{
        let value = normalizeContactSignatureText_(item && item.value)
        if (value){
            signatures[`e:${value}`] = true
        }
    })

    let phones = Array.isArray(memberData.phoneNumbers) ? memberData.phoneNumbers : []
    phones.forEach(item=>{
        let value = normalizeContactSignaturePhone_(item && item.value)
        if (value){
            signatures[`p:${value}`] = true
        }
    })

    let names = Array.isArray(memberData.names) ? memberData.names : []
    names.forEach(item=>{
        let value = normalizeContactSignatureText_(
            (item && item.displayName) || (item && item.unstructuredName)
        )
        if (value){
            signatures[`n:${value}`] = true
        }
    })

    if (!Object.keys(signatures).length){
        let fallbackPayload = sanitizeContactForCreate_(memberData)
        delete fallbackPayload.memberships
        signatures[`h:${computeHash_(fallbackPayload)}`] = true
    }

    return Object.keys(signatures)
}

function getPeopleDataBatchAs_(service, resourceNames){
    let out = {}
    if (!resourceNames || !resourceNames.length){
        return out
    }
    let fields = encodeURIComponent("names,emailAddresses,phoneNumbers")
    let baseApi = `https://people.googleapis.com/v1/people:batchGet?personFields=${fields}`
    let chunks = chunkResourceNamesByUrlLength_(resourceNames, baseApi, true, 1800)
    chunks.forEach(chunk=>{
        let query = chunk.map(name=>`resourceNames=${encodeURIComponent(name)}`).join("&")
        let api = `${baseApi}&${query}`
        let params = {
            method: "get",
            muteHttpExceptions: true,
        }
        let response = fetchWithService_(service, api, params)
        let code = response.getResponseCode()
        if (code < 200 || code >= 300){
            return
        }
        let data = JSON.parse(response.getContentText())
        let responses = data.responses || []
        responses.forEach(item=>{
            if (item.httpStatusCode && item.httpStatusCode !== 200){
                return
            }
            let person = item.person
            if (!person || !person.resourceName){
                return
            }
            out[person.resourceName] = person
        })
    })
    return out
}

function getMissingSourceRowsForRecipientGroup_(service, sharedGroup, sourceRows){
    if (!service || !sharedGroup || !sourceRows || !sourceRows.length){
        return []
    }
    let groupId = sharedGroup.created
    if (!groupId){
        return []
    }
    let details = getGroupDetailsAs_(service, groupId, 2000)
    let memberResourceNames = details.memberResourceNames || []
    if (!memberResourceNames.length){
        return sourceRows.slice()
    }
    let peopleById = getPeopleDataBatchAs_(service, memberResourceNames)
    if (!Object.keys(peopleById).length){
        return []
    }
    let signatureCounts = {}
    memberResourceNames.forEach(resourceName=>{
        let person = peopleById[resourceName]
        let signatures = buildContactSignatures_(person)
        signatures.forEach(signature=>{
            signatureCounts[signature] = Number(signatureCounts[signature] || 0) + 1
        })
    })
    let missingRows = []
    sourceRows.forEach(row=>{
        let signatures = buildContactSignatures_(row.memberData)
        let match = signatures.find(signature=>Number(signatureCounts[signature] || 0) > 0)
        if (match){
            signatureCounts[match] = Number(signatureCounts[match] || 0) - 1
            return
        }
        missingRows.push(row)
    })
    return missingRows
}

function listSkippedContactsForOwnerGroup(ownerKey, resourceName){
    let ownerAppData = getAppData(ownerKey) || {}
    let ownerGroups = ownerAppData.groups || {}
    let ownerGroup = ownerGroups[resourceName]
    if (!ownerGroup){
        return {count: 0, contacts: []}
    }
    let recipients = ownerGroup.shared || []
    let contacts = []
    let rowsByShareId = {}
    let seen = {}
    let pushContact = (item)=>{
        let key = `${item.recipientEmail}|${item.sheetRow}|${item.index}|${item.name}|${item.email}|${item.ownerPersonId || ""}`
        if (seen[key]){
            return
        }
        seen[key] = true
        contacts.push(item)
    }
    recipients.forEach(recipientEmail=>{
        let beforeCount = contacts.length
        let recipientAppData = getAppData(recipientEmail) || {}
        let recipientSharedGroups = recipientAppData.sharedGroups || []
        let sharedGroup = recipientSharedGroups.find(item=>
            item &&
            item.owner === ownerKey &&
            item.resourceName === resourceName
        )
        if (!sharedGroup){
            return
        }
        let skippedIndexes = Array.isArray(sharedGroup.skippedIndexes) ? sharedGroup.skippedIndexes : []
        let skippedCount = Number(sharedGroup.skippedCount || 0)
        let ownerState = sharedGroup.ownerSyncState || {}
        let skippedOwnerIds = Array.isArray(ownerState.skippedOwnerIds) ? Array.from(new Set(ownerState.skippedOwnerIds.filter(Boolean))) : []
        let retryAttempts = ownerState.retryAttempts || sharedGroup.retryAttempts || {}
        let rows = []
        let rowsByIndex = {}
        let ownerIndexById = {}
        let mappingByOwnerId = {}
        if (sharedGroup.shareId){
            if (!rowsByShareId[sharedGroup.shareId]){
                try {
                    rowsByShareId[sharedGroup.shareId] = readSharedContactRows_(sharedGroup.shareId)
                } catch (e) {
                    rowsByShareId[sharedGroup.shareId] = []
                }
            }
            rows = rowsByShareId[sharedGroup.shareId] || []
            rows.forEach(row=>{
                rowsByIndex[row.memberIndex] = row
            })
            try {
                let mappingData = loadSyncMappings_(sharedGroup.shareId, ownerKey, resourceName, recipientEmail)
                mappingByOwnerId = mappingData.byOwnerId || {}
            } catch (e) {}
        }
        if (skippedOwnerIds.length){
            try {
                let details = getGroupDetails_(resourceName, 2000)
                let memberResourceNames = details.memberResourceNames || []
                memberResourceNames.forEach((ownerPersonId, idx)=>{
                    ownerIndexById[ownerPersonId] = idx
                })
            } catch (e) {}
            skippedOwnerIds.forEach(ownerPersonId=>{
                let memberData = null
                try {
                    memberData = getPeopleData(ownerPersonId, false)
                } catch (e) {}
                let mapping = mappingByOwnerId[ownerPersonId] || {}
                let email = (
                    memberData &&
                    memberData.emailAddresses &&
                    memberData.emailAddresses.length &&
                    memberData.emailAddresses[0].value
                ) ? memberData.emailAddresses[0].value : ""
                pushContact({
                    recipientEmail,
                    sheetRow: mapping.rowNumber || "",
                    index: (ownerIndexById[ownerPersonId] !== undefined) ? Number(ownerIndexById[ownerPersonId]) : -1,
                    name: memberData ? getContactDisplayName_(memberData) : "(skipped contact details unavailable)",
                    email,
                    ownerPersonId
                })
            })
        }
        if (!sharedGroup.shareId){
            if (skippedIndexes.length){
                skippedIndexes.forEach(indexValue=>{
                    pushContact({
                        recipientEmail,
                        sheetRow: "",
                        index: Number(indexValue),
                        name: "(skipped contact details unavailable)",
                        email: "",
                        ownerPersonId: ""
                    })
                })
                return
            }
            if (skippedCount > 0){
                for (let i = 0; i < skippedCount; i++){
                    pushContact({
                        recipientEmail,
                        sheetRow: "",
                        index: -1,
                        name: "(skipped contact details unavailable)",
                        email: "",
                        ownerPersonId: ""
                    })
                }
            }
            return
        }
        if (!skippedIndexes.length){
            // Backward-compatible fallback: some older records tracked only count or retry map.
            let retryKeys = Object.keys(retryAttempts).map(value=>Number(value)).filter(value=>!isNaN(value))
            if (retryKeys.length){
                skippedIndexes = retryKeys
            } else if (skippedCount > 0){
                skippedIndexes = []
            } else {
                return
            }
        }

        skippedIndexes.forEach(indexValue=>{
            let index = Number(indexValue)
            let row = rowsByIndex[index]
            let memberData = row ? row.memberData : null
            let email = (
                memberData &&
                memberData.emailAddresses &&
                memberData.emailAddresses.length &&
                memberData.emailAddresses[0].value
            ) ? memberData.emailAddresses[0].value : ""
            pushContact({
                recipientEmail,
                sheetRow: row ? row.sheetRow : "",
                index,
                name: getContactDisplayName_(memberData),
                email,
                ownerPersonId: ""
            })
        })

        // Strong fallback: compare source rows to recipient group membership and return
        // actual missing source rows even when skippedIndexes is stale or incomplete.
        if (rows.length){
            try {
                let service = getRecipientService_(recipientEmail)
                if (service.hasAccess()){
                    let missingRows = getMissingSourceRowsForRecipientGroup_(service, sharedGroup, rows)
                    missingRows.forEach(row=>{
                        let memberData = row.memberData
                        let email = (
                            memberData &&
                            memberData.emailAddresses &&
                            memberData.emailAddresses.length &&
                            memberData.emailAddresses[0].value
                        ) ? memberData.emailAddresses[0].value : ""
                        pushContact({
                            recipientEmail,
                            sheetRow: row.sheetRow,
                            index: row.memberIndex,
                            name: getContactDisplayName_(memberData),
                            email,
                            ownerPersonId: ""
                        })
                    })
                } else if (skippedCount > 0 && !skippedIndexes.length){
                    for (let i = 0; i < skippedCount; i++){
                        pushContact({
                            recipientEmail,
                            sheetRow: "",
                            index: -1,
                            name: "(skipped contact details unavailable)",
                            email: "",
                            ownerPersonId: ""
                        })
                    }
                }
            } catch (e) {
                if (skippedCount > 0 && !skippedIndexes.length){
                    for (let i = 0; i < skippedCount; i++){
                        pushContact({
                            recipientEmail,
                            sheetRow: "",
                            index: -1,
                            name: "(skipped contact details unavailable)",
                            email: "",
                            ownerPersonId: ""
                        })
                    }
                }
            }
        }
        if (contacts.length === beforeCount && skippedCount > 0){
            for (let i = 0; i < skippedCount; i++){
                pushContact({
                    recipientEmail,
                    sheetRow: "",
                    index: -1,
                    name: "(skipped contact details unavailable)",
                    email: "",
                    ownerPersonId: ""
                })
            }
        }
    })
    contacts.sort((a, b)=>{
        let emailA = String(a.recipientEmail || "")
        let emailB = String(b.recipientEmail || "")
        if (emailA !== emailB){
            return emailA.localeCompare(emailB)
        }
        return Number(a.index || 0) - Number(b.index || 0)
    })
    return {
        count: contacts.length,
        contacts
    }
}



function getGroupMembers(resourceName, maxMembers=30){
//    resourceName = "contactGroups/651c2d590e225410"
    let token = ScriptApp.getOAuthToken()
    
    let api = "https://people.googleapis.com/v1/" + resourceName + "?maxMembers=" + maxMembers

    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      muteHttpExceptions: true,
    }
    
    let data = fetchJson_(api, params)
    
    let members = data.memberResourceNames || []
    return members
}

function getGroupDetails_(resourceName, maxMembers=2000){
    let token = ScriptApp.getOAuthToken()
    let api = "https://people.googleapis.com/v1/" + resourceName + "?maxMembers=" + maxMembers
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      muteHttpExceptions: true,
    }
    return fetchJson_(api, params)
}

function groupExists_(resourceName){
    if (!resourceName){
        return false
    }
    try {
        getGroupDetails_(resourceName, 1)
        return true
    } catch (e) {
        return false
    }
}

function getGroupMemberCountSafe_(groupResourceName){
    if (!groupResourceName){
        return null
    }
    try {
        let details = getGroupDetails_(groupResourceName, 2000)
        let members = details.memberResourceNames || []
        return members.length
    } catch (e) {
        return null
    }
}

function getPeopleData(resourceName, useCache=true){
//    resourceName = "people/c4587819774469148078"
    if (useCache){
        let cached = getCachedPerson_(resourceName)
        if (cached){
            return cached
        }
    }
    let token = ScriptApp.getOAuthToken()
    //https://developers.google.com/people/api/rest/v1/people/get#query-parameters
    let personFields = [
        "addresses",
        "biographies",
        "birthdays",
        "emailAddresses",
        "memberships",
        "names",
        "nicknames",
        "organizations",
        "phoneNumbers",
        "photos",
        "relations",
        "urls",
        "userDefined"
    ].join(",")
    let api = "https://people.googleapis.com/v1/" + resourceName + "?personFields=" + personFields

    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      muteHttpExceptions: true,
    }
    
    let data = fetchJson_(api, params)

    delete(data.etag)
    delete(data.resourceName)
    Object.keys(data).forEach(key=>{
        let items = data[key]
        if (!Array.isArray(items)){
            return
        }
        items.forEach((item, index)=>{
            if (data[key][index].metadata){
                delete(data[key][index].metadata)
            }
            if (data[key][index].formattedType){
                delete(data[key][index].formattedType)
            }
        })
    })
    
    setCachedPerson_(resourceName, data)

    return data
}

function normalizeSharedFormattedAddress_(value){
    let text = String(value || "").replace(/\r/g, "\n").trim()
    if (!text || text.indexOf("\n") < 0){
        return text
    }
    let lines = text.split("\n").map(line=>String(line || "").trim()).filter(Boolean)
    if (lines.length === 2 && /,\s*[A-Z]{2}\s+\d{5}/.test(lines[1])){
        return `${lines[0]}, ${lines[1]}`
    }
    return lines.join("\n")
}

function sanitizeContactForCreate_(data){
    if (!data){
        return {}
    }
    let allowedKeys = [
        "addresses",
        "biographies",
        "birthdays",
        "emailAddresses",
        "memberships",
        "names",
        "nicknames",
        "organizations",
        "phoneNumbers",
        "relations",
        "urls",
        "userDefined"
    ]
    let clean = {}
    allowedKeys.forEach(key=>{
        if (data[key]){
            clean[key] = data[key]
        }
    })

    // Remove commonly read-only/derived fields that can cause create/update rejections.
    if (Array.isArray(clean.names)){
        clean.names = clean.names.map(item=>{
            let next = Object.assign({}, item || {})
            delete next.displayName
            delete next.displayNameLastFirst
            delete next.unstructuredName
            return next
        })
    }
    if (Array.isArray(clean.phoneNumbers)){
        clean.phoneNumbers = clean.phoneNumbers.map(item=>{
            let next = Object.assign({}, item || {})
            delete next.canonicalForm
            return next
        })
    }
    if (Array.isArray(clean.addresses)){
        clean.addresses = clean.addresses.map(item=>{
            let next = Object.assign({}, item || {})
            delete next.formattedType
            let structuredKeys = [
                "streetAddress",
                "extendedAddress",
                "poBox",
                "city",
                "region",
                "postalCode",
                "country",
                "countryCode",
            ]
            let structured = {}
            structuredKeys.forEach(key=>{
                let value = String(next[key] || "").trim()
                if (value){
                    structured[key] = value
                }
                delete next[key]
            })
            let formatted = String(next.formattedValue || "").trim()
            if (!formatted){
                let parts = [
                    structured.streetAddress || "",
                    structured.extendedAddress || "",
                    structured.city || "",
                    structured.region || "",
                    structured.postalCode || "",
                ].filter(Boolean)
                formatted = parts.join(", ")
            }
            formatted = normalizeSharedFormattedAddress_(formatted)
            // Keep structured fields so recipient Google Contacts match the owner.
            Object.keys(structured).forEach(key=>{
                next[key] = structured[key]
            })
            if (formatted){
                next.formattedValue = formatted
            } else {
                delete next.formattedValue
            }
            return next
        })
    }
    if (Array.isArray(clean.birthdays)){
        let seen = {}
        clean.birthdays = clean.birthdays.filter(item=>{
            if (!item || !item.date){
                return false
            }
            let y = item.date.year || ""
            let m = item.date.month || ""
            let d = item.date.day || ""
            let key = `${y}-${m}-${d}`
            if (seen[key]){
                return false
            }
            seen[key] = true
            return true
        })
    }
    return clean
}

function buildSharedGroupName_(groupName){
    let base = String(groupName || "").trim()
    if (/\(shared\)$/i.test(base)){
        return base
    }
    return `${base} (shared)`
}

function createNewGroup(groupName){
//    groupName = "test 1"
    let api = "https://people.googleapis.com/v1/contactGroups"
    let token = ScriptApp.getOAuthToken()
    
    let name = buildSharedGroupName_(groupName)
    
    let payload = {"contactGroup":{"name": name}}
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      method: "post",
      muteHttpExceptions: true,
    }
    
    let data = fetchJson_(api, params)
    if (data.resourceName){
        return data.resourceName
    }
}

function createNewGroupAs_(service, groupName){
//    groupName = "test 1"
    let api = "https://people.googleapis.com/v1/contactGroups"
    let name = buildSharedGroupName_(groupName)
    let payload = {"contactGroup":{"name": name}}
    let params = {
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      method: "post",
      muteHttpExceptions: true,
    }
    let response = fetchWithService_(service, api, params)
    let code = response.getResponseCode()
    let text = response.getContentText()
    if (code < 200 || code >= 300){
        throw new Error(`People API error (${code}) for ${api}: ${text}`)
    }
    let data = JSON.parse(text)
    if (data.resourceName){
        return data.resourceName
    }
}


function createNewMember(memberData, groupId, options){
//    memberData = {names:[], emailAddresses:[], phoneNumbers:[]}
//    groupId = "contactGroups/3a25e45c09dbde35"
    options = options || {}
    let api = "https://people.googleapis.com/v1/people:createContact"
    let token = ScriptApp.getOAuthToken()
   
    if (!memberData){
        return
    }
    let payload = sanitizeContactForCreate_(memberData)
    payload.memberships = [
        {
            "contactGroupMembership": {
              "contactGroupResourceName": groupId
            }
        }
    ]
    
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      method: "post",
      muteHttpExceptions: true,
    }
    
    let data = fetchJson_(api, params)

    if (data.resourceName){
        if (!options.skipPhoto){
            setContactPhoto_(data.resourceName, memberData)
        }
        return data.resourceName
    }
}

function createNewMemberAs_(service, memberData, groupId, options){
    options = options || {}
    if (!memberData){
        return
    }
    let api = "https://people.googleapis.com/v1/people:createContact"
    let payload = sanitizeContactForCreate_(memberData)
    payload.memberships = [
        {
            "contactGroupMembership": {
              "contactGroupResourceName": groupId
            }
        }
    ]
    let params = {
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      method: "post",
      muteHttpExceptions: true,
    }
    let response = fetchWithService_(service, api, params)
    let code = response.getResponseCode()
    let text = response.getContentText()
    if (code < 200 || code >= 300){
        throw new Error(`People API error (${code}) for ${api}: ${text}`)
    }
    let data = JSON.parse(text)
    if (data.resourceName){
        if (!options.skipPhoto){
            setContactPhotoAs_(service, data.resourceName, memberData)
        }
        return data.resourceName
    }
}

function hasContactPhoto_(memberData){
    if (!memberData || !Array.isArray(memberData.photos) || !memberData.photos.length){
        return false
    }
    let first = memberData.photos[0] || {}
    return !!String(first.url || "").trim()
}

function ensureContactInGroupAs_(service, groupId, resourceName){
    if (!groupId || !resourceName){
        return
    }
    let normalized = normalizePersonResourceName_(resourceName)
    let api = "https://people.googleapis.com/v1/" + groupId + "/members:modify"
    let params = {
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify({
            resourceNamesToAdd: [normalized]
        }),
        muteHttpExceptions: true,
    }
    // People API treats "already in group" as success/no-op.
    fetchJson_(api, Object.assign({}, params, {
        headers: {
            Authorization: "Bearer " + service.getAccessToken()
        }
    }))
}

function removeContactFromGroupAs_(service, groupId, resourceName){
    if (!groupId || !resourceName){
        return
    }
    let normalized = normalizePersonResourceName_(resourceName)
    let api = "https://people.googleapis.com/v1/" + groupId + "/members:modify"
    let params = {
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify({
            resourceNamesToRemove: [normalized]
        }),
        muteHttpExceptions: true,
    }
    fetchJson_(api, Object.assign({}, params, {
        headers: {
            Authorization: "Bearer " + service.getAccessToken()
        }
    }))
}

function deleteContact_(resourceName){
    if (!resourceName){
        return
    }
    let api = "https://people.googleapis.com/v1/" + resourceName + ":deleteContact"
    let token = ScriptApp.getOAuthToken()
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      method: "delete",
      muteHttpExceptions: true,
    }
    UrlFetchApp.fetch(api, params)
}

function setContactPhoto_(resourceName, memberData){
    if (!memberData || !memberData.photos || !memberData.photos.length){
        return
    }
    let url = memberData.photos[0].url
    if (!url){
        return
    }
    try {
        let photoBytes = fetchPhotoBytes_(url, ScriptApp.getOAuthToken())
        if (!photoBytes){
            return
        }
        let normalized = normalizePersonResourceName_(resourceName)
        let api = "https://people.googleapis.com/v1/" + normalized + ":updateContactPhoto"
        let params = {
          headers: {
            Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
          },
          method: "patch",
          contentType: "application/json",
          payload: JSON.stringify({photoBytes, personFields: "photos"}),
          muteHttpExceptions: true,
        }
        let resp = UrlFetchApp.fetch(api, params)
        if (resp.getResponseCode() < 200 || resp.getResponseCode() >= 300){
            Logger.log("updateContactPhoto failed: %s", resp.getContentText())
        }
    } catch (e) {
        // ignore photo errors
    }
}

function setContactPhotoAs_(service, resourceName, memberData, options){
    options = options || {}
    let throwOnError = !!options.throwOnError
    if (!memberData || !memberData.photos || !memberData.photos.length){
        return
    }
    let url = memberData.photos[0].url
    if (!url){
        return
    }
    try {
        let photoBytes = fetchPhotoBytes_(url, ScriptApp.getOAuthToken())
        if (!photoBytes){
            return
        }
        let normalized = normalizePersonResourceName_(resourceName)
        let api = "https://people.googleapis.com/v1/" + normalized + ":updateContactPhoto"
        let params = {
          method: "patch",
          contentType: "application/json",
          payload: JSON.stringify({photoBytes, personFields: "photos"}),
          muteHttpExceptions: true,
        }
        let resp = fetchWithService_(service, api, params)
        if (resp.getResponseCode() < 200 || resp.getResponseCode() >= 300){
            let errorMessage = `People API error (${resp.getResponseCode()}) while updating contact photo: ${resp.getContentText()}`
            if (throwOnError){
                throw new Error(errorMessage)
            }
            Logger.log("updateContactPhoto as recipient failed: %s", resp.getContentText())
        }
    } catch (e) {
        if (throwOnError){
            throw e
        }
        // ignore photo errors
    }
}

function buildPhotoUrlCandidates_(url){
    let raw = String(url || "").trim()
    if (!raw){
        return []
    }
    let out = [raw]

    // People photo URLs often accept query size override.
    if (raw.includes("?")){
        if (/([?&])sz=\d+/i.test(raw)){
            out.push(raw.replace(/([?&])sz=\d+/i, "$1sz=0"))
            out.push(raw.replace(/([?&])sz=\d+/i, "$1sz=2048"))
        } else {
            out.push(raw + "&sz=0")
            out.push(raw + "&sz=2048")
        }
    } else {
        out.push(raw + "?sz=0")
        out.push(raw + "?sz=2048")
    }

    // Some Google-hosted image URLs use suffix size tokens like "=s100-c".
    if (/=s\d+/i.test(raw)){
        out.push(raw.replace(/=s\d+(-[a-z0-9]+)?/i, "=s0"))
        out.push(raw.replace(/=s\d+(-[a-z0-9]+)?/i, "=s2048"))
    } else {
        out.push(raw + "=s0")
        out.push(raw + "=s2048")
    }

    // De-duplicate while preserving order.
    let seen = {}
    return out.filter(candidate=>{
        if (!candidate || seen[candidate]){
            return false
        }
        seen[candidate] = true
        return true
    })
}

function fetchPhotoBytes_(url, token){
    let candidates = buildPhotoUrlCandidates_(url)
    for (let i = 0; i < candidates.length; i++){
        try {
            let response = UrlFetchApp.fetch(candidates[i], {
                headers: {
                    Authorization: 'Bearer ' + token,
                },
                muteHttpExceptions: true,
            })
            if (response.getResponseCode() >= 200 && response.getResponseCode() < 300){
                return Utilities.base64Encode(response.getBlob().getBytes())
            }
        } catch (e) {
            // try next candidate
        }
    }
    return null
}

function normalizePersonResourceName_(resourceName){
    if (!resourceName){
        return resourceName
    }
    return resourceName.startsWith("people/") ? resourceName : `people/${resourceName}`
}

function deleteContactAs_(service, resourceName){
    if (!resourceName){
        return
    }
    let api = "https://people.googleapis.com/v1/" + resourceName + ":deleteContact"
    let params = {
      method: "delete",
      muteHttpExceptions: true,
    }
    fetchWithService_(service, api, params)
}

function deleteContactsInGroup_(groupId){
    if (!groupId){
        return
    }
    try {
        let details = getGroupDetails_(groupId, 2000)
        let members = details.memberResourceNames || []
        members.forEach(member=>{
            deleteContact_(member)
        })
    } catch (e) {
        // ignore cleanup errors
    }
}

function getGroupDetailsAs_(service, groupId, maxMembers=2000){
    let api = "https://people.googleapis.com/v1/" + groupId + "?maxMembers=" + maxMembers
    let params = {
      method: "get",
      muteHttpExceptions: true,
    }
    let response = fetchWithService_(service, api, params)
    let code = response.getResponseCode()
    let text = response.getContentText()
    if (code < 200 || code >= 300){
        throw new Error(`People API error (${code}) for ${api}: ${text}`)
    }
    return JSON.parse(text)
}

function listContactGroupsAs_(service, pageSize=200){
    let api = `https://people.googleapis.com/v1/contactGroups?pageSize=${pageSize}`
    let params = {
      method: "get",
      muteHttpExceptions: true,
    }
    let response = fetchWithService_(service, api, params)
    let code = response.getResponseCode()
    let text = response.getContentText()
    if (code < 200 || code >= 300){
        throw new Error(`People API error (${code}) for ${api}: ${text}`)
    }
    return JSON.parse(text)
}

function findGroupByNameAs_(service, groupName){
    let data = listContactGroupsAs_(service, 200)
    let groups = data.contactGroups || []
    let match = groups.find(g => g.name === groupName)
    return match ? match.resourceName : null
}

function deleteContactsInGroupAs_(service, groupId){
    if (!groupId){
        return
    }
    try {
        let details = getGroupDetailsAs_(service, groupId, 2000)
        let members = details.memberResourceNames || []
        members.forEach(member=>{
            deleteContactAs_(service, member)
        })
    } catch (e) {
        // ignore cleanup errors
    }
}

function findGroupByName_(groupName){
    let token = ScriptApp.getOAuthToken()
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      muteHttpExceptions: true,
    }
    let baseApi = "https://people.googleapis.com/v1/contactGroups?pageSize=200"
    let nextPageToken
    do {
        let api = nextPageToken ? `${baseApi}&pageToken=${encodeURIComponent(nextPageToken)}` : baseApi
        let data = fetchJson_(api, params)
        let groups = data.contactGroups || []
        let match = groups.find(g => g && g.name === groupName)
        if (match && match.resourceName){
            return match.resourceName
        }
        nextPageToken = data.nextPageToken
    } while (nextPageToken)
    return null
}

function createSharedGroups(sharedGroups, options){
    if (!sharedGroups || !sharedGroups.length){
        return sharedGroups || []
    }
    options = options || {}
    let startedAt = Date.now()
    let recipientService = options.recipientService || null

    let findGroupByNameFn = recipientService
        ? (groupName)=>findGroupByNameAs_(recipientService, groupName)
        : (groupName)=>findGroupByName_(groupName)
    let createNewGroupFn = recipientService
        ? (groupName)=>createNewGroupAs_(recipientService, groupName)
        : (groupName)=>createNewGroup(groupName)
    let getGroupDetailsFn = recipientService
        ? (groupId, maxMembers)=>getGroupDetailsAs_(recipientService, groupId, maxMembers)
        : (groupId, maxMembers)=>getGroupDetails_(groupId, maxMembers)
    let groupExistsFn = (groupId)=>{
        if (!groupId){
            return false
        }
        try {
            getGroupDetailsFn(groupId, 1)
            return true
        } catch (e) {
            return false
        }
    }
    let getGroupMemberCountSafeFn = (groupId)=>{
        if (!groupId){
            return null
        }
        try {
            let details = getGroupDetailsFn(groupId, 2000)
            let members = details.memberResourceNames || []
            return members.length
        } catch (e) {
            return null
        }
    }
    let createNewMemberFn = recipientService
        ? (memberData, groupId, createOptions)=>createNewMemberAs_(recipientService, memberData, groupId, createOptions)
        : (memberData, groupId, createOptions)=>createNewMember(memberData, groupId, createOptions)

    let deduped = []
    let indexByKey = {}
    sharedGroups.forEach(group=>{
        if (!group){
            return
        }
        let key = `${group.owner || ""}|${group.resourceName || ""}`
        if (indexByKey[key] === undefined){
            indexByKey[key] = deduped.length
            deduped.push(group)
            return
        }
        let idx = indexByKey[key]
        let current = deduped[idx] || {}
        deduped[idx] = Object.assign({}, current, group, {
            created: group.created || current.created || "",
            importCursor: group.importCursor || current.importCursor || 0
        })
    })

    let remainingOps = (typeof options.maxOps === "number") ? options.maxOps : RECIPIENT_IMPORT_MAX_OPS_PER_LOAD_
    if (remainingOps <= 0){
        deduped.forEach((group, index)=>{
            let total = Number(group.memberCount || 0)
            let cursor = Number(group.importCursor || 0)
            if (!group.status){
                if (total > 0 && cursor < total){
                    deduped[index].status = `Queued (${cursor}/${total})`
                } else if (total > 0 && cursor >= total){
                    deduped[index].status = "Shared"
                }
            }
        })
        return deduped.filter(group=>{
            if (!group){
                return false
            }
            if (!group.shareId){
                return false
            }
            return true
        })
    }
    deduped.forEach((group, index)=>{
        let {name, shareId, created, importCursor} = group
        try {
            let membersData = readSharedContacts_(shareId)
            if (!membersData.length){
                if (created && groupExistsFn(created)){
                    let actualCount = getGroupMemberCountSafeFn(created)
                    if (actualCount !== null){
                        deduped[index].actualCount = actualCount
                    }
                    deduped[index].status = "Shared"
                } else {
                    deduped[index].status = "Missing data"
                    deduped[index].staleRemove = true
                }
                return
            }

            let groupId = created
            if (!groupId){
                try {
                    groupId = createNewGroupFn(name)
                } catch (e) {
                    if (String(e).includes("ALREADY_EXISTS")){
                        groupId = findGroupByNameFn(buildSharedGroupName_(name))
                    } else {
                        throw e
                    }
                }
                if (!groupId){
                    deduped[index].status = "Error"
                    return
                }
                deduped[index].created = groupId
                deduped[index].importCursor = 0
                importCursor = 0
            }

            let actualCount = getGroupMemberCountSafeFn(groupId)
            if (actualCount !== null){
                deduped[index].actualCount = actualCount
            }

            if ((importCursor === undefined || importCursor === null || importCursor === "") && groupId){
                try {
                    let details = getGroupDetailsFn(groupId, 2000)
                    let existingMembers = details.memberResourceNames || []
                    deduped[index].importCursor = existingMembers.length
                    importCursor = existingMembers.length
                } catch (e) {
                    deduped[index].importCursor = 0
                    importCursor = 0
                }
            }

            let start = Math.max(0, Number(importCursor || deduped[index].importCursor || 0))
            let retryIndexes = Array.isArray(deduped[index].retryIndexes) ? deduped[index].retryIndexes.slice() : []
            let retryAttempts = deduped[index].retryAttempts || {}
            let skippedIndexes = Array.isArray(deduped[index].skippedIndexes) ? deduped[index].skippedIndexes.slice() : []
            let photoPendingIndexes = Array.isArray(deduped[index].photoPendingIndexes) ? deduped[index].photoPendingIndexes.slice() : []
            let photoRetryIndexes = Array.isArray(deduped[index].photoRetryIndexes) ? deduped[index].photoRetryIndexes.slice() : []
            let photoRetryAttempts = deduped[index].photoRetryAttempts || {}
            let photoTargets = deduped[index].photoTargets || {}
            let photoDoneCount = Number(deduped[index].photoDoneCount || 0)
            let skippedCount = skippedIndexes.length
            let needsMoreCreate = start < membersData.length || retryIndexes.length > 0
            if (!needsMoreCreate){
                deduped[index].importCursor = membersData.length
            } else {
                if (remainingOps <= 0){
                    deduped[index].status = retryIndexes.length
                        ? `Queued (${start}/${membersData.length}, retry ${retryIndexes.length})`
                        : `Queued (${start}/${membersData.length})`
                    return
                }

                let opsForThisGroup = Math.min(RECIPIENT_IMPORT_BATCH_SIZE_, remainingOps)
                let end = start
                let failed = 0
                let attemptsUsed = 0

                // Retry previously failed contacts first.
                while (retryIndexes.length && attemptsUsed < opsForThisGroup){
                    if ((Date.now() - startedAt) > (options.timeBudgetMs || RECIPIENT_IMPORT_TIME_BUDGET_MS_)){
                        break
                    }
                    let retryIndex = retryIndexes.shift()
                    let memberData = membersData[retryIndex]
                    if (!memberData){
                        continue
                    }
                    try {
                        let createdId = createNewMemberFn(memberData, groupId, {skipPhoto: true})
                        if (RECIPIENT_IMPORT_INCLUDE_PHOTOS_ && createdId && hasContactPhoto_(memberData)){
                            photoTargets[String(retryIndex)] = createdId
                            if (!photoPendingIndexes.includes(retryIndex) && !photoRetryIndexes.includes(retryIndex)){
                                photoPendingIndexes.push(retryIndex)
                            }
                        }
                        delete retryAttempts[String(retryIndex)]
                        skippedIndexes = skippedIndexes.filter(idx => Number(idx) !== Number(retryIndex))
                    } catch (e) {
                        failed++
                        let key = String(retryIndex)
                        let nextCount = Number(retryAttempts[key] || 0) + 1
                        if (nextCount >= RECIPIENT_IMPORT_MAX_RETRIES_){
                            delete retryAttempts[key]
                            if (!skippedIndexes.includes(retryIndex)){
                                skippedIndexes.push(retryIndex)
                            }
                        } else {
                            retryAttempts[key] = nextCount
                            retryIndexes.push(retryIndex)
                        }
                    }
                    attemptsUsed++
                }

                while (start < membersData.length && attemptsUsed < opsForThisGroup){
                    if ((Date.now() - startedAt) > (options.timeBudgetMs || RECIPIENT_IMPORT_TIME_BUDGET_MS_)){
                        break
                    }
                    let currentIndex = start
                    try {
                        let createdId = createNewMemberFn(membersData[currentIndex], groupId, {skipPhoto: true})
                        if (RECIPIENT_IMPORT_INCLUDE_PHOTOS_ && createdId && hasContactPhoto_(membersData[currentIndex])){
                            photoTargets[String(currentIndex)] = createdId
                            if (!photoPendingIndexes.includes(currentIndex) && !photoRetryIndexes.includes(currentIndex)){
                                photoPendingIndexes.push(currentIndex)
                            }
                        }
                        skippedIndexes = skippedIndexes.filter(idx => Number(idx) !== Number(currentIndex))
                    } catch (e) {
                        failed++
                        let key = String(currentIndex)
                        let nextCount = Number(retryAttempts[key] || 0) + 1
                        if (nextCount >= RECIPIENT_IMPORT_MAX_RETRIES_){
                            delete retryAttempts[key]
                            if (!skippedIndexes.includes(currentIndex)){
                                skippedIndexes.push(currentIndex)
                            }
                        } else {
                            retryAttempts[key] = nextCount
                            retryIndexes.push(currentIndex)
                        }
                    }
                    start++
                    attemptsUsed++
                }
                end = start
                remainingOps -= attemptsUsed

                deduped[index].importCursor = end
                deduped[index].retryIndexes = Array.from(new Set(retryIndexes))
                deduped[index].retryAttempts = retryAttempts
                deduped[index].skippedIndexes = Array.from(new Set(skippedIndexes))
                deduped[index].skippedCount = deduped[index].skippedIndexes.length
                deduped[index].photoPendingIndexes = Array.from(new Set(photoPendingIndexes))
                deduped[index].photoRetryIndexes = Array.from(new Set(photoRetryIndexes))
                deduped[index].photoRetryAttempts = photoRetryAttempts
                deduped[index].photoTargets = photoTargets
                deduped[index].photoDoneCount = photoDoneCount
                skippedCount = deduped[index].skippedCount
                actualCount = getGroupMemberCountSafeFn(groupId)
                if (actualCount !== null){
                    deduped[index].actualCount = actualCount
                }
                if (end < membersData.length || retryIndexes.length > 0){
                    let retryLabel = retryIndexes.length ? `, retry ${retryIndexes.length}` : ""
                    let failLabel = failed ? `, ${failed} failed this pass` : ""
                    deduped[index].status = `Importing (${end}/${membersData.length}${retryLabel}${failLabel})`
                    return
                }
            }

            // Phase 2: apply photos in smaller batches so contact creation remains fast/stable.
            if (RECIPIENT_IMPORT_INCLUDE_PHOTOS_){
                let setPhotoFn = recipientService
                    ? (recipientId, memberData)=>setContactPhotoAs_(recipientService, recipientId, memberData, {throwOnError: true})
                    : (recipientId, memberData)=>setContactPhoto_(recipientId, memberData)
                let photoAttemptsUsed = 0
                let photoFailures = 0
                let photoOpsForThisGroup = Math.min(
                    RECIPIENT_IMPORT_PHOTO_BATCH_SIZE_,
                    Math.max(1, remainingOps)
                )

                while (photoRetryIndexes.length && photoAttemptsUsed < photoOpsForThisGroup){
                    if ((Date.now() - startedAt) > (options.timeBudgetMs || RECIPIENT_IMPORT_TIME_BUDGET_MS_)){
                        break
                    }
                    let sourceIndex = Number(photoRetryIndexes.shift())
                    let memberData = membersData[sourceIndex]
                    let recipientId = photoTargets[String(sourceIndex)]
                    if (!memberData || !recipientId){
                        delete photoRetryAttempts[String(sourceIndex)]
                        continue
                    }
                    try {
                        setPhotoFn(recipientId, memberData)
                        delete photoRetryAttempts[String(sourceIndex)]
                        photoDoneCount++
                    } catch (e) {
                        photoFailures++
                        let key = String(sourceIndex)
                        let nextCount = Number(photoRetryAttempts[key] || 0) + 1
                        if (nextCount >= RECIPIENT_IMPORT_MAX_RETRIES_){
                            delete photoRetryAttempts[key]
                        } else {
                            photoRetryAttempts[key] = nextCount
                            photoRetryIndexes.push(sourceIndex)
                        }
                    }
                    photoAttemptsUsed++
                }

                while (photoPendingIndexes.length && photoAttemptsUsed < photoOpsForThisGroup){
                    if ((Date.now() - startedAt) > (options.timeBudgetMs || RECIPIENT_IMPORT_TIME_BUDGET_MS_)){
                        break
                    }
                    let sourceIndex = Number(photoPendingIndexes.shift())
                    let memberData = membersData[sourceIndex]
                    let recipientId = photoTargets[String(sourceIndex)]
                    if (!memberData || !recipientId){
                        delete photoRetryAttempts[String(sourceIndex)]
                        continue
                    }
                    try {
                        setPhotoFn(recipientId, memberData)
                        delete photoRetryAttempts[String(sourceIndex)]
                        photoDoneCount++
                    } catch (e) {
                        photoFailures++
                        let key = String(sourceIndex)
                        let nextCount = Number(photoRetryAttempts[key] || 0) + 1
                        if (nextCount >= RECIPIENT_IMPORT_MAX_RETRIES_){
                            delete photoRetryAttempts[key]
                        } else {
                            photoRetryAttempts[key] = nextCount
                            photoRetryIndexes.push(sourceIndex)
                        }
                    }
                    photoAttemptsUsed++
                }

                remainingOps -= photoAttemptsUsed
                deduped[index].photoPendingIndexes = Array.from(new Set(photoPendingIndexes))
                deduped[index].photoRetryIndexes = Array.from(new Set(photoRetryIndexes))
                deduped[index].photoRetryAttempts = photoRetryAttempts
                deduped[index].photoTargets = photoTargets
                deduped[index].photoDoneCount = photoDoneCount

                let photoRemaining = deduped[index].photoPendingIndexes.length + deduped[index].photoRetryIndexes.length
                let photoTotal = photoDoneCount + photoRemaining
                if (photoRemaining > 0){
                    let failLabel = photoFailures ? `, ${photoFailures} photo failures this pass` : ""
                    deduped[index].status = `Importing photos (${photoDoneCount}/${photoTotal}${failLabel})`
                    return
                }
            }

            deduped[index].status = skippedCount ? `Shared (${skippedCount} skipped)` : "Shared"
        } catch (e) {
            deduped[index].status = "Error: " + (e && e.message ? e.message : "Unable to process shared group")
        }
    })

    return deduped.filter(group=>{
        if (!group){
            return false
        }
        if (!group.shareId){
            return false
        }
        if (group.staleRemove){
            return false
        }
        // Remove stale invitation entries when there is no backing shared data and no created group.
        if (group.status === "Missing data" && !group.created){
            return false
        }
        return true
    })
}

function getContactDisplayName_(memberData){
    if (!memberData){
        return "(unknown)"
    }
    if (memberData.names && memberData.names.length && memberData.names[0].displayName){
        return memberData.names[0].displayName
    }
    if (memberData.emailAddresses && memberData.emailAddresses.length && memberData.emailAddresses[0].value){
        return memberData.emailAddresses[0].value
    }
    if (memberData.phoneNumbers && memberData.phoneNumbers.length && memberData.phoneNumbers[0].value){
        return memberData.phoneNumbers[0].value
    }
    return "(unnamed contact)"
}

function getSharedImportSkippedContacts(){
    let profile = getProfile()
    let appData = getAppData(profile.email) || {}
    let sharedGroups = appData.sharedGroups || []
    let contacts = []
    sharedGroups.forEach(group=>{
        let skippedIndexes = Array.isArray(group.skippedIndexes) ? group.skippedIndexes : []
        if (!skippedIndexes.length || !group.shareId){
            return
        }
        let membersData = []
        try {
            membersData = readSharedContacts_(group.shareId)
        } catch (e) {
            membersData = []
        }
        skippedIndexes.forEach(idx=>{
            let index = Number(idx)
            let memberData = membersData[index]
            let primaryEmail = (
                memberData &&
                memberData.emailAddresses &&
                memberData.emailAddresses.length &&
                memberData.emailAddresses[0].value
            ) ? memberData.emailAddresses[0].value : ""
            contacts.push({
                groupName: group.name || "",
                owner: group.owner || "",
                memberIndex: index,
                name: getContactDisplayName_(memberData),
                email: primaryEmail
            })
        })
    })
    return {
        count: contacts.length,
        contacts
    }
}

function deleteGroup_(groupId){
    let api = "https://people.googleapis.com/v1/" + groupId
    let token = ScriptApp.getOAuthToken()
    let params = {
      headers: {
        Authorization: 'Bearer ' + token,
      },
      method: "delete",
      muteHttpExceptions: true,
    }
    UrlFetchApp.fetch(api, params)
}

function deleteGroupAs_(service, groupId){
    let api = "https://people.googleapis.com/v1/" + groupId
    let params = {
      method: "delete",
      muteHttpExceptions: true,
    }
    fetchWithService_(service, api, params)
}
