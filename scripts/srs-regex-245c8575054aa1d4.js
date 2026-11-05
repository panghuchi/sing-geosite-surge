// Generated hostname matcher. No DNS queries, network calls, or MITM.
var patterns = ["^chatgpt-async-webps-prod-\\S+-\\d+\\.webpubsub\\.azure\\.com$"];
var host = typeof $request.hostname === "string" ? $request.hostname : "";
var matched = host !== "" && patterns.some(function (pattern) {
  return new RegExp(pattern).test(host);
});
$done({matched: matched});
