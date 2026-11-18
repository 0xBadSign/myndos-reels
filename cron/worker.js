const REPO = "0xBadSign/myndos-reels";

async function dispatch(env, workflow, command) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "myndos-reels-cron",
    },
    body: JSON.stringify({ ref: "main", inputs: { command } }),
  });
  console.log(workflow, command, r.status, r.status === 204 ? "" : await r.text());
}

export default {
  async scheduled(event, env) {
    if (!env.GH_TOKEN) return console.log("GH_TOKEN is not set");
    if (event.cron === "11 16 * * *") await dispatch(env, "post.yml", "daily");
    else await dispatch(env, "autodm.yml", "run");
  },
};
