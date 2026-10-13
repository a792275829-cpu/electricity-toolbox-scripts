// Reserve the backend before downloading; release it on every error path.
export async function installUpdate(
  reserve: () => Promise<unknown>,
  download: () => Promise<void>,
  restart: () => Promise<void>,
  release: () => Promise<unknown>,
) {
  await reserve();
  try {
    await download();
    await restart();
  } finally {
    await release();
  }
}
