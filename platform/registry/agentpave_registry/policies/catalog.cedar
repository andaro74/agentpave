// Who may invoke which tool.
//
// Cedar is default-deny: an agent that matches no `permit` is denied without
// anything here having to say so. That is why there is no "deny everyone else"
// rule — writing one would imply the default is the other way round.

// The catalog agent may invoke the catalogue tools.
permit (
    principal == Agent::"catalog-agent",
    action == Action::"invoke",
    resource in ToolGroup::"catalog"
);

// Nothing may invoke a tool that changes state, whatever else permits it.
//
// A Cedar `forbid` overrides every `permit`, so this holds even if someone
// adds a write-classed tool to the catalog group and grants it explicitly.
// The registry's consequence class is checked by the contract suite and
// enforced here — the label has to be true in two places to matter.
forbid (
    principal,
    action == Action::"invoke",
    resource
)
when { resource.consequence != "read" };
