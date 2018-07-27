from __future__ import print_function, division, unicode_literals
import numpy as np
import os
os.chdir('/gpfs01/bethge/home/oeberle/Scripts/deepgaze/')
import sys
sys.path.append('/gpfs01/bethge/home/oeberle/Scripts/deepgaze/tf_steerable_pyramid/')
import scipy
from scipy import misc
import scipy.optimize as optimize
import pickle
from steerable_pyramid import rCosFn, get_basis, tf_fftshift, tf_ifftshift, tf_factorial
import tensorflow as tf

def get_W_rand(std=0.2, filter_size = 15):
    return tf.random_normal([filter_size,filter_size,1,1], stddev=std)


class SteerableCNN(object):
    """ Steerable CNN implementation """
    def __init__(self,  filter_size, n_filters, n_bands, height, im_ds, ds = True, trainable = True):
        self.filter_size = filter_size
        self.n_filters = n_filters
        self.n_bands = n_bands
        self.height = height
        self.ds = ds
        self.im_ds = im_ds
        # Placeholders for input
        self.input_x = tf.placeholder(tf.int32, name="input_x", shape = (None,None,None,None))
        self.trainable = trainable
        # Load or Create filters
        self.filters = self.get_steerable_filters()
        #self.filters = self.get_gabor_filters()

        # Initialize filters randomly
           
        self.W_lp = tf.Variable(get_W_rand() , name = 'SteerablePyramid/W_lp', trainable =  self.trainable)
        
        self.W_hp =  tf.Variable(get_W_rand(), name = 'SteerablePyramid/W_hp', trainable =  self.trainable)
        
        self.W_orients_real = [tf.Variable(get_W_rand(), name = 'SteerablePyramid/W_orients_{}_real'.format(i), trainable =  self.trainable )for i in range(self.n_bands)]
        
        self.W_orients_imag =  [tf.Variable(get_W_rand(), name = 'SteerablePyramid/W_orients_{}_imag'.format(i), trainable =  self.trainable) for i in range(self.n_bands)]
        

  
    def get_steerable_filters(self, path = '/gpfs01/bethge/home/oeberle/Results/Steerable_filters/'):
        try:
            filters = pickle.load(open(path + 'filters_{}_{}_{}.p'.format(self.filter_size, self.n_filters, self.n_bands), 'rb'), encoding = 'latin1')

        except IOError:
            filters = get_optimized_filters(self.filter_size, self.n_bands)
            pickle.dump(filters, open(path + 'filters_{}_{}_{}.p'.format(self.filter_size, self.n_filters, self.n_bands), 'wb'))
        return filters   
    
    
    def get_gabor_filters(self):
        print('USE GABORS')
 
        try:
            filters = pickle.load(open('/gpfs01/bethge/home/oeberle/Results/Steerable_filters/gabor_filters_15_1_arange_0_180_22.5_freq_0.25_rot_mult_5.p', 'rb'), encoding = 'latin1')
            

        except IOError:
            print('No Gabor filters found!')
        return filters  
    
    def init_filters(self, sess,  level = 0):
        
        highpass_filter_real = self.filters[level]['highpass_real']
        lowpass_filter_real = self.filters[level]['lowpass_real']
        
        orientation_filters_real = self.filters[level]['orientation_real']
        
        orientation_filters_imag = self.filters[level]['orientation_imag']
        
        sess.run(self.W_lp.assign(tf.cast(lowpass_filter_real[:,:,np.newaxis,np.newaxis],tf.float32)))
        
        sess.run(self.W_hp.assign(tf.cast(highpass_filter_real[:,:,np.newaxis,np.newaxis],tf.float32)))
                 
        for i in range(self.n_bands):          
            sess.run(self.W_orients_real[i].assign(tf.cast(orientation_filters_real[i][:,:,np.newaxis,np.newaxis],tf.float32)))
                 
            sess.run(self.W_orients_imag[i].assign(tf.cast(orientation_filters_imag[i][:,:,np.newaxis,np.newaxis],tf.float32)))
                            

        
    def build_steerable_pyramid(self, input_image):
       
        #Downsample input image by factor of 2
        
        if self.im_ds == 2:
            print('SP: Image downsampling  by factor of 2!')
            _image = tf.nn.avg_pool(input_image, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME')
        else:
            print('SP: No image downsampling!')
            _image = input_image

        levels = []
        for level in range(self.height):
            levels.append(self.steerable_layer(_image,l=level))
            if self.ds == True:
                print('SP: Downsampling by factor 2.')
                _image = tf.nn.avg_pool(levels[-1]['lowpass_feature_real'], ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME')
            else:
                _image = levels[-1]['lowpass_feature_real']
                print('SP: No downsampling.')
                
        return levels


    def steerable_layer(self, input_image,l ,level = 0):

        highpass_feature_real = self.highpass_layer(input_image, self.W_hp, l , 'real')
        lowpass_feature_real = self.lowpass_layer( input_image, self.W_lp,l, 'real')

        orientation_features_real = self.orientation_layer(lowpass_feature_real, self.W_orients_real,l, 'real')
        orientation_features_imag = self.orientation_layer(lowpass_feature_real, self.W_orients_imag,l, 'imag') 

        packed_orientations_real = tf.pack(orientation_features_real, axis=-1)
        packed_orientations_imag = tf.pack(orientation_features_imag, axis=-1)

        data = { 
                'lowpass_filter_real': self.W_hp,
                'lowpass_feature_real': lowpass_feature_real,
                'highpass_filter_real': self.W_hp,
                'highpass_feature_real': highpass_feature_real,

                'orientations_real': orientation_features_real,
                'packed_orientations_real': packed_orientations_real,
                'orientation_filters_real': self.W_orients_real,
            
                'orientations_imag': orientation_features_imag,
                'packed_orientations_imag': packed_orientations_imag,
                'orientation_filters_imag': self.W_orients_imag,
            }
        

        return data


    def highpass_layer(self, input_image, highpass_filter,l, part):
        highpass_feature = tf.nn.conv2d(input_image, highpass_filter, strides=[1, 1, 1, 1], padding="SAME")
        return highpass_feature



    def lowpass_layer(self, input_image, lowpass_filter,l, part):
        lowpass_feature = tf.nn.conv2d(input_image, lowpass_filter, strides=[1, 1, 1, 1], padding="SAME")
        return lowpass_feature

    def orientation_layer(self, input_image, orientation_filters,l,part):
        orientation_features = []
        
        for b in range(self.n_bands):
            orientation_feature = tf.nn.conv2d(input_image, orientation_filters[b], strides=[1, 1, 1, 1], padding="SAME")
            orientation_features.append(orientation_feature)

        return orientation_features #, orientation_filters
    
    
    
    def steerable_network_for_single_feature(self, input_feature, twidth):# , twidth=1, n_bands=4, height=5):
        """ returns a networ containing a steerable pyramid for a single feature"""
        levels = self.build_steerable_pyramid(input_feature)
        net = {
            'input': input_feature,
        }
        for k, level in enumerate(levels):
            praefix = 'level{}_'.format(k)
            net[praefix+'lowpass_real'] = level['lowpass_feature_real']
            net[praefix+'highpass_real'] = level['highpass_feature_real']
            net[praefix+'orientations_real'] = level['packed_orientations_real']
            net[praefix+'orientations_imag'] = level['packed_orientations_imag']

        return net


    def steerable_network_for_multiple_features(self, input_features, twidth=1, n_features=None):#, , n_bands=4, height=5, n_features=None):
        """ Create a steerable pyramid for each of the features and concatenate the pyramid into one pyramid"""
        if n_features is None:
            n_features = int(input_features.get_shape()[-1])

        nets = []
        for i in range(n_features):
            feature = input_features[:, :, :, i, np.newaxis]
            net = self.steerable_network_for_single_feature(feature, twidth=twidth)
            nets.append(net)

        def get_features(name):
            return [n[name] for n in nets]

        net = {'input': input_features}
        for k in range(self.height):
            praefix = 'level{}_'.format(k)
            net[praefix+'lowpass_real'] = tf.squeeze(tf.pack(get_features(praefix+'lowpass_real'), axis=-1), [3])
            net[praefix+'highpass_real'] = tf.squeeze(tf.pack(get_features(praefix+'highpass_real'), axis=-1), [3])
            net[praefix+'orientations_real'] = tf.concat(3,get_features(praefix+'orientations_real'))
            net[praefix+'orientations_imag'] = tf.concat(3,get_features(praefix+'orientations_imag'))
            
            for b in range(self.n_bands):
                net[praefix+'orientations_real_{}'.format(b)] = net[praefix+'orientations_real'][:,:,:,:,b]
                net[praefix+'orientations_imag_{}'.format(b)] = net[praefix+'orientations_imag'][:,:,:,:,b]
        return net

    
    def get_tf_pyramid(self):
        g = tf.Graph()
        
        with g.as_default():
            input_image = tf.placeholder(tf.float32, [None,None,None,None])#, shape=[None, None,None,None])
            levels = self.build_steerable_pyramid(input_image)

        return input_image, levels
    
########### Filter Optimization functions ##################    
    

def optimize_filter(input_image, target_image, filter_size = 15):
    ''' Returns optimized filter'''
    
    def f(W_value):
        reshaped_W_value = np.reshape(W_value,(filter_size,filter_size))
        assign_op = W.assign(reshaped_W_value)
        k = sess.run(assign_op)
        v, g = sess.run([loss, loss_grad], feed_dict={
            X: input_image[np.newaxis, :,  :, np.newaxis],
            target: target_image[np.newaxis, :, :, np.newaxis]})
        return v, g.flatten()

    #Define graph
    X = tf.placeholder(tf.float32, shape=(None, None, None, 1))
    target = tf.placeholder(tf.float32, shape=(None, None, None, 1))
    W = tf.Variable(tf.cast(np.random.rand(filter_size,filter_size), tf.float32))
    W_4d = tf.reshape(W, shape=[filter_size, filter_size, 1, 1])
    conv = tf.nn.conv2d(X, W_4d, strides=[1, 1, 1, 1], padding='SAME')
    loss = tf.reduce_mean(tf.square(conv - target))
    loss_grad, = tf.gradients(loss, [W])


    with tf.Session() as sess:
        tf.initialize_all_variables().run()
        W0 = np.random.rand(filter_size,filter_size)        
        res = optimize.minimize(f, W0.flatten() , jac=True)
        return np.reshape(res['x'], (filter_size,filter_size))


def get_optimized_filters(filter_size, n_bands, twidth = 1, dtype = tf.float32):
    level = 0

    input_image = scipy.misc.face().mean(axis=2)[100:500,100:500]
    
    input_image_fft = np.fft.fft2(input_image)
    input_image_fft = np.fft.fftshift(input_image_fft)
    im_shape = np.shape(input_image)[0], np.shape(input_image)[1]
    a, b = get_basis(im_shape[1], im_shape[0]) #width, height
    
    with tf.Session() as sess2:
        tf.initialize_all_variables().run()
        angle = sess2.run(a)
        log_radius = sess2.run(b)
        
    optimized_filters = {level: {'highpass_real':None, 'highpass_imag':None,'lowpass_real':None, 'lowpass_imag':None, 
                                 'orientation_real':{b:None for b in range(n_bands)},  'orientation_imag':{b:None for b in range(n_bands)}} }

    with tf.Session() as sess:
        tf.initialize_all_variables().run() 

        #  for level in range(height):
        print('level', level)
        rcos = rCosFn(log_radius, width=twidth, x0=-twidth/2-level, shift=0, factor=1)

        # Get High and Lowpass
        rcos = rCosFn(log_radius, width=twidth, x0=-twidth/2-level, shift=0, factor=1)

        high_pass_filter = tf.sqrt(rcos)
        low_pass_filter = tf.sqrt(1-rcos)

        low_pass_feature_fft = input_image_fft*tf.cast(low_pass_filter, tf.complex64)
        high_pass_feature_fft = input_image_fft*tf.cast(high_pass_filter, tf.complex64)

        high_pass_feature_fft_spatial =  tf.ifft2d(tf_ifftshift(high_pass_feature_fft))
        low_pass_feature_fft_spatial =  tf.ifft2d(tf_ifftshift(low_pass_feature_fft))


        optimized_filters[level]['highpass_real'] = optimize_filter(input_image, sess.run(tf.real(high_pass_feature_fft_spatial)), filter_size)
        optimized_filters[level]['lowpass_real'] = optimize_filter(input_image, sess.run(tf.real(low_pass_feature_fft_spatial)), filter_size)

        optimized_filters[level]['highpass_imag'] = optimize_filter(input_image, sess.run(tf.imag(high_pass_feature_fft_spatial)), filter_size)
        optimized_filters[level]['lowpass_imag'] = optimize_filter(input_image, sess.run(tf.imag(low_pass_feature_fft_spatial)), filter_size)

        # Get Orientation filters
        order = n_bands - 1

        orientation_radius_filter = rCosFn(log_radius, width=twidth, x0=-twidth/2-level, shift=0, factor=1)
        orientation_radius_filter = tf.sqrt(orientation_radius_filter)
        const = tf.cast(tf.pow(2, 2*order) * tf.square(tf_factorial(order)) / (n_bands * tf_factorial(2*order)), dtype)

        orientation_features_real = []
        orientation_features_imag = []
        angle_masks = []
        orientation_filters = []

        for b in range(n_bands):
            print('band', b)

            with tf.name_scope('orientation_{}'.format(b)):
                angle_shift = angle - np.pi*b/n_bands

                # The original code reads'
                # > alpha = (angle_shift + np.pi) % (2*np.pi) - np.pi
                # but tensorflows mod and numpys mod behave differently for negative numbers. Therefor we add
                # some 2*2*pi
                alpha = tf.mod((angle_shift + np.pi + 4*np.pi), (2*np.pi)) - np.pi
                angle_mask = 2.0 * tf.sqrt(const) * tf.pow(tf.cos(angle_shift), order) * tf.cast(tf.abs(alpha) < np.pi/2, dtype)
                angle_masks.append(angle_mask)

                orientation_filter = tf.cast(tf.pow(np.complex(0, -1), n_bands - 1), tf.complex64) * tf.cast(
                    angle_mask * orientation_radius_filter, tf.complex64)

                orientation_filters.append(orientation_filter)

                band_fft = input_image_fft * orientation_filter

                band = tf.ifft2d(tf_ifftshift(band_fft))

                orientation_feature_real = sess.run(tf.real(band))
                orientation_feature_imag = sess.run(tf.imag(band))

                orientation_features_real.append(orientation_feature_real)
                orientation_features_imag.append(orientation_feature_imag)

                optimized_filters[level]['orientation_real'][b] = optimize_filter(input_image, orientation_feature_real, filter_size)
                optimized_filters[level]['orientation_imag'][b] = optimize_filter(input_image, orientation_feature_imag, filter_size)

    return optimized_filters
    
    
if __name__ == '__main__':
    print('Steerable CNN')