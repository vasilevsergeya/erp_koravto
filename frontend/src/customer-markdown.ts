import type { Root, RootContent, Parent } from "mdast";

export function linkCustomerNumbers() {
  function walk(parent: Parent) {
    parent.children = parent.children.flatMap((child): RootContent[] => {
      if (child.type === "text") {
        return child.value
          .split(/(?<![\p{L}\p{N}_])([CС]-?[0-9]+)(?![\p{L}\p{N}_])/giu)
          .map((s, i) =>
            i % 2
              ? {
                  type: "link",
                  url: "/?client=" + s,
                  children: [{ type: "text", value: s }],
                }
              : { type: "text", value: s },
          );
      }
      if (
        "children" in child &&
        child.type !== "link" &&
        child.type !== "linkReference"
      )
        walk(child);
      return [child];
    });
  }
  return (tree: Root) => walk(tree);
}
