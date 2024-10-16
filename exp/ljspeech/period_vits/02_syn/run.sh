#!/bin/bash

bin_dir=../../../../src/x_vits/bin

model_name=period_vits

HYDRA_FULL_ERROR=1 TOKENIZERS_PARALLELISM=false python ${bin_dir}/synthesize.py \
    generator=${model_name} \
    lit_module=period_vits \
    dataset=single_en \
    path=ljspeech \
    mel=ljspeech
