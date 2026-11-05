/**
 * Policy passages begin with a citation such as "Time & Attendance Policy §2.9: ...".
 * The § sign means "section"; show it in words and keep the citation apart from the rule text.
 */
export function splitPolicyText(text: string): { source: string | null; body: string } {
  const match = /^(.{3,80}?)\s*§\s*([\d.]+)\s*:\s*([\s\S]+)$/.exec(text);
  if (match) {
    return { source: `${match[1].trim()} · Section ${match[2].replace(/\.$/, "")}`, body: match[3].trim() };
  }
  return { source: null, body: text.replaceAll(/§\s*/g, "Section ") };
}
