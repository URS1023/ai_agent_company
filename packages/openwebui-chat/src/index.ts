import { mount, unmount } from "svelte";
import ChatIsland from "./ChatIsland.svelte";
import type { MountNativeChat, ChatProps } from "../public";
import theme from "./theme.css?inline";
export { reduceNativeFrame } from "./reducer";
export const mountNativeChat: MountNativeChat = (host, props) => {
  const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" }),
    style = document.createElement("style"),
    container = document.createElement("div");
  style.textContent = theme;
  container.className = "owui-island-root";
  shadow.replaceChildren(style, container);
  const component = mount(ChatIsland, {
    target: container,
    props: { data: props },
  }) as unknown as { update: (value: ChatProps) => void };
  return {
    update: (value) => component.update(value),
    unmount: () => {
      void unmount(component);
      shadow.replaceChildren();
    },
  };
};
export { readNativeEvents } from "./events";
