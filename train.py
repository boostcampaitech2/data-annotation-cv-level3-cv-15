import os
import os.path as osp
import time
import math
from datetime import timedelta
from argparse import ArgumentParser

import torch
from torch import cuda
from torch.utils.data import DataLoader
from torch.optim import lr_scheduler
from tqdm import tqdm

from east_dataset import EASTDataset
from dataset import SceneTextDataset
from model import EAST

import wandb
import numpy as np
from sklearn.metrics import f1_score


def parse_args():
    parser = ArgumentParser()

    # Conventional args
    parser.add_argument('--data_dir', type=str,
                        default=os.environ.get('SM_CHANNEL_TRAIN', '../input/data/ICDAR17_Korean'))
    parser.add_argument('--model_dir', type=str, default=os.environ.get('SM_MODEL_DIR',
                                                                        'trained_models'))

    parser.add_argument('--device', default='cuda' if cuda.is_available() else 'cpu')
    parser.add_argument('--num_workers', type=int, default=4)

    parser.add_argument('--image_size', type=int, default=1024)
    parser.add_argument('--input_size', type=int, default=512)
    parser.add_argument('--batch_size', type=int, default=12)
    parser.add_argument('--learning_rate', type=float, default=1e-3)
    parser.add_argument('--max_epoch', type=int, default=200)
    parser.add_argument('--save_interval', type=int, default=5)

    # gg
    parser.add_argument('--json_file_name', type=str, default='train')


    args = parser.parse_args()

    if args.input_size % 32 != 0:
        raise ValueError('`input_size` must be a multiple of 32')

    return args


def do_training(data_dir, model_dir, device, image_size, input_size, num_workers, batch_size,
                learning_rate, max_epoch, save_interval, json_file_name):
    valid_avail = False
    try:
        int(json_file_name[-1])
        valid_avail = True
    except Exception as e:
        print(e)

    dataset = SceneTextDataset(data_dir, split=json_file_name, image_size=image_size, crop_size=input_size)
    dataset = EASTDataset(dataset)

    num_batches = math.ceil(len(dataset) / batch_size)
    train_loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model = EAST()
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    scheduler = lr_scheduler.MultiStepLR(optimizer, milestones=[max_epoch // 2], gamma=0.1)

    #model.train()
    for epoch in range(max_epoch):
        epoch_loss, epoch_start = 0, time.time()
        model.train()
        with tqdm(total=num_batches) as pbar:
            for img, gt_score_map, gt_geo_map, roi_mask in train_loader:
                pbar.set_description('[Epoch {}]'.format(epoch + 1))

                loss, extra_info = model.train_step(img, gt_score_map, gt_geo_map, roi_mask)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                loss_val = loss.item()
                epoch_loss += loss_val

                pbar.update(1)
                val_dict = {
                    'Cls loss': extra_info['cls_loss'], 'Angle loss': extra_info['angle_loss'],
                    'IoU loss': extra_info['iou_loss']
                }

                pbar.set_postfix(val_dict)

        scheduler.step()

        print('Mean loss: {:.4f} | Elapsed time: {}'.format(
            epoch_loss / num_batches, timedelta(seconds=time.time() - epoch_start)))

        val_dict.update({"Mean loss": epoch_loss / num_batches})

        # validation
        if valid_avail:
            valid_json_file_name = "valid_" + json_file_name[-1]
            v_dataset = SceneTextDataset(data_dir, split=valid_json_file_name, image_size=image_size, crop_size=input_size)
            v_dataset = EASTDataset(v_dataset)
            valid_loader = DataLoader(v_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
            model.eval()
            epoch_loss, epoch_start, epoch_f1 = 0, time.time(), 0
            with tqdm(total=num_batches) as pbar:
                for img, gt_score_map, gt_geo_map, roi_mask in valid_loader:
                    pbar.set_description('[Epoch {}]'.format(epoch + 1))

                    loss, extra_info = model.train_step(img, gt_score_map, gt_geo_map, roi_mask)
                    #optimizer.zero_grad()
                    #loss.backward()
                    #optimizer.step()

                    loss_val = loss.item()
                    epoch_loss += loss_val

                    pbar.update(1)

                    val_dict_1 = {
                        'Valid_Cls loss': extra_info['cls_loss'], 'Valid_Angle loss': extra_info['angle_loss'],
                        'Valid_IoU loss': extra_info['iou_loss']
                    }

                    pbar.set_postfix(val_dict_1)
                
            print('Valid_Mean valid_loss: {:.4f} | Elapsed time: {}'.format(
            epoch_loss / num_batches, timedelta(seconds=time.time() - epoch_start)))

            val_dict_1.update({"Valid_Mean loss": epoch_loss / num_batches})
            val_dict.update(val_dict_1)

        wandb.log(val_dict)

        if (epoch + 1) % save_interval == 0:
            if not osp.exists(model_dir):
                os.makedirs(model_dir)

            ckpt_fpath = osp.join(model_dir, 'latest.pth')
            torch.save(model.state_dict(), ckpt_fpath)

def main(args):
    wandb.init(project="ocr",  entity="ptop", config=args, name = 'hi')
    np.random.seed(15)
    do_training(**args.__dict__)

if __name__ == '__main__':
    args = parse_args()
    main(args)