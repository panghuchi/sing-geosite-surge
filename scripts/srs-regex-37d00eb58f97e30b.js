// Generated hostname matcher. No DNS queries, network calls, or MITM.
var patterns = ["^(.+\\.)*zh\\.okaapps\\.com$", "^cdn\\d-epicgames-\\d+\\.file\\.myqcloud\\.com$", "^epicgames-download\\d-\\d+\\.file\\.myqcloud\\.com$", "^r+[0-9]+(---|\\.)sn-(2x3|ni5|j5o)\\w{5}\\.googlevideo\\.com$", "^r+[0-9]+(---|\\.)sn-(2x3|ni5|j5o)\\w{5}\\.xn--ngstr-lra8j\\.com$"];
var host = typeof $request.hostname === "string" ? $request.hostname : "";
var matched = host !== "" && patterns.some(function (pattern) {
  return new RegExp(pattern).test(host);
});
$done({matched: matched});
