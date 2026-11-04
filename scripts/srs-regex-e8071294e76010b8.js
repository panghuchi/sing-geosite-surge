// Generated hostname matcher. No DNS queries, network calls, or MITM.
var patterns = ["(^|\\.)[1-9]+aipai\\.(com|net)$", "(^|\\.)[1-9]+zipai\\.(com|net)$", "(^|\\.)fi11sm([0-9]+)?\\.com$", "(^|\\.)javdb\\d+\\.com$", ".+\\.dkr\\.ecr\\.[^\\.]+\\.amazonaws\\.com$", "^[0-9]+vod-adaptive\\.akamaized\\.net$", "^chatgpt-async-webps-prod-\\S+-\\d+\\.webpubsub\\.azure\\.com$", "^ewcdn[0-9]+\\.nowe\\.com$"];
var host = typeof $request.hostname === "string" ? $request.hostname : "";
var matched = host !== "" && patterns.some(function (pattern) {
  return new RegExp(pattern).test(host);
});
$done({matched: matched});
