import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "@fortawesome/fontawesome-free/css/fontawesome.min.css";
import "@fortawesome/fontawesome-free/css/solid.min.css";
import "@fortawesome/fontawesome-free/css/regular.min.css";
import App from "./App.vue";
import { router } from "./router";
import "./styles/tokens.css";
import "./assets/main.css";
import "./styles/components.css";
import "./modules/chat/chat.css";
import "./modules/knowledge/knowledge.css";
import "./modules/flows/flows.css";
import "./modules/agents/agents.css";

createApp(App)
  .use(createPinia())
  .use(router)
  .use(ElementPlus, { locale: zhCn })
  .mount("#app");
