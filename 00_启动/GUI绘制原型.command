#!/bin/zsh
# Reuse the normal launcher and portable Python setup for this scoped preview.
export TOOLBOX_RENDER_PROTOTYPE=1
exec "${0:A:h}/电力工具箱.command"
